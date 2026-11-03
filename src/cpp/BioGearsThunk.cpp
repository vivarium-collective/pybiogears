// Licensed under the Apache License, Version 2.0.
#include "BioGearsThunk.h"

#include <biogears/cdm/engine/PhysiologyEngine.h>
#include <biogears/cdm/engine/PhysiologyEngineTrack.h>
#include <biogears/cdm/enums/SEAnesthesiaEnums.h>
#include <biogears/cdm/enums/SEPatientEnums.h>
#include <biogears/cdm/properties/SEScalarTime.h>
#include <biogears/cdm/properties/SEUnitScalar.h>
#include <biogears/cdm/patient/actions/SEHemorrhage.h>
#include <biogears/cdm/scenario/SEAction.h>
#include <biogears/cdm/scenario/SEScenario.h>
#include <biogears/cdm/scenario/requests/SEDataRequestManager.h>
#include <biogears/cdm/scenario/requests/SEEnvironmentDataRequest.h>
#include <biogears/cdm/scenario/requests/SEEquipmentDataRequest.h>
#include <biogears/cdm/scenario/requests/SEGasCompartmentDataRequest.h>
#include <biogears/cdm/scenario/requests/SELiquidCompartmentDataRequest.h>
#include <biogears/cdm/scenario/requests/SEPatientDataRequest.h>
#include <biogears/cdm/scenario/requests/SEPhysiologyDataRequest.h>
#include <biogears/cdm/scenario/requests/SESubstanceDataRequest.h>
#include <biogears/cdm/scenario/requests/SEThermalCompartmentDataRequest.h>
#include <biogears/cdm/scenario/requests/SETissueCompartmentDataRequest.h>
#include <biogears/cdm/substance/SESubstanceCompound.h>
#include <biogears/cdm/substance/SESubstanceConcentration.h>
#include <biogears/cdm/substance/SESubstanceManager.h>
#include <biogears/cdm/utils/Logger.h>
#include <biogears/cdm/utils/SEEventHandler.h>
#include <biogears/cdm/circuit/fluid/SEFluidCircuitNode.h>
#include <biogears/cdm/circuit/fluid/SEFluidCircuitPath.h>
#include <biogears/engine/BioGearsPhysiologyEngine.h>
#include <biogears/engine/Controller/BioGears.h>
#include <biogears/engine/Controller/BioGearsCircuits.h>
#include <biogears/version.h>

#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>

namespace fs = std::filesystem;
using namespace biogears;

namespace pybiogears {

// ---------------------------------------------------------------------------
class ThunkLogForward : public LoggerForward {
public:
  void Debug(char const* msg) const override { push("Debug", msg); }
  void Info(char const* msg) const override { push("Info", msg); }
  void Warning(char const* msg) const override { push("Warning", msg); }
  void Error(char const* msg) const override { push("Error", msg); }
  void Fatal(char const* msg) const override { push("Fatal", msg); }
  std::vector<LogMessage> drain()
  {
    std::vector<LogMessage> out;
    out.swap(m_messages);
    return out;
  }

private:
  void push(const char* level, const char* msg) const { m_messages.push_back({ level, msg ? msg : "" }); }
  mutable std::vector<LogMessage> m_messages;
};

// ---------------------------------------------------------------------------
class ThunkEventHandler : public SEEventHandler {
public:
  void HandlePatientEvent(SEPatientEventType type, bool active, const SEScalarTime* time) override
  {
    m_events.push_back({ ToString(type), active, time ? time->GetValue(TimeUnit::s) : std::nan("") });
  }
  void HandleAnesthesiaMachineEvent(SEAnesthesiaMachineEvent type, bool active, const SEScalarTime* time) override
  {
    m_events.push_back({ ToString(type), active, time ? time->GetValue(TimeUnit::s) : std::nan("") });
  }
  std::vector<EventChange> drain()
  {
    std::vector<EventChange> out;
    out.swap(m_events);
    return out;
  }

private:
  std::vector<EventChange> m_events;
};

// ---------------------------------------------------------------------------
BioGearsThunk::BioGearsThunk(const std::string& working_dir, const std::string& log_file)
  : m_working_dir(working_dir)
  , m_log_forward(std::make_unique<ThunkLogForward>())
  , m_event_handler(std::make_unique<ThunkEventHandler>())
{
  if (!fs::is_directory(working_dir))
    throw std::runtime_error("BioGears working directory does not exist: " + working_dir);
  m_engine = CreateBioGearsEngine(working_dir, log_file);
  if (!m_engine)
    throw std::runtime_error("CreateBioGearsEngine returned null");
  set_log_to_console(false);
  m_engine->GetLogger()->SetForward(m_log_forward.get());
  m_engine->SetEventHandler(m_event_handler.get());
  // The thunk reads its requests itself (pull_data). With auto-tracking on,
  // every AdvanceModelTime sample re-runs PhysiologyEngineTrack::SetupRequests,
  // which re-connects every request (resetting its unit to the engine's native
  // unit and leaking a SEDataRequestScalar each time) and tries to write a CSV.
  m_engine->SetAutoTrackFlag(false);
}

BioGearsThunk::~BioGearsThunk()
{
  if (m_engine) {
    m_engine->GetLogger()->SetForward(nullptr);
    m_engine->SetEventHandler(nullptr);
  }
}

std::string BioGearsThunk::version() { return full_version_string(); }

std::string BioGearsThunk::temp_path(const std::string& stem, const std::string& ext) const
{
  // Inside the working dir so BioGears' path resolution finds it; unique per thunk.
  std::ostringstream ss;
  ss << m_working_dir << "/.pybiogears_tmp_" << stem << "_" << static_cast<const void*>(this) << "_"
     << const_cast<BioGearsThunk*>(this)->m_tmp_counter++ << ext;
  return ss.str();
}

// --- state -----------------------------------------------------------------
// BioGearsEngine::LoadState copies the engine's existing data requests across
// the reload, but marshals them as the base CDM::DataRequestData, which its own
// factory rejects ("Unsupported DataRequest Received", and a null request is
// added to the manager). The thunk re-creates its requests after every load,
// so drop them first.
void BioGearsThunk::drop_requests()
{
  m_engine->GetEngineTrack()->GetDataRequestManager().Invalidate();
  m_se_requests.clear();
  m_direct.clear();
  m_connected.clear();
}

// BioGears 8.2.0's io::Substance::UnMarshall(SubstanceCompoundData) appends a
// compound's components to the ones already loaded from substances/*.xml
// without clearing them, so after LoadState an active compound (e.g. Saline)
// lists every component twice and an infusion delivers double the solutes
// (probe P3). Both copies hold the same concentration; keep the first.
// LoadState rebuilds the patient, and BioGears forwards patient events to the
// handler only when it is set (or at the end of InitializeEngine), so the
// handler silently drops off after every load (probe P7). Re-attach it.
void BioGearsThunk::after_load()
{
  m_engine->SetEventHandler(m_event_handler.get());
  dedupe_compound_components();
  connect_requests();
}

void BioGearsThunk::dedupe_compound_components()
{
  for (SESubstanceCompound* c : m_engine->GetSubstanceManager().GetCompounds()) {
    std::vector<SESubstanceConcentration>& comps = c->GetComponents();
    std::set<std::string> seen;
    std::vector<SESubstanceConcentration> unique;
    for (auto& sc : comps)
      if (seen.insert(sc.GetSubstance().Name).second)
        unique.push_back(sc);
    if (unique.size() != comps.size())
      comps = unique;
  }
}

bool BioGearsThunk::load_state(const std::string& path, double sim_time_s)
{
  drop_requests();
  bool ok;
  if (sim_time_s >= 0) {
    SEScalarTime t;
    t.SetValue(sim_time_s, TimeUnit::s);
    ok = m_engine->LoadState(path, &t);
  } else {
    ok = m_engine->LoadState(path);
  }
  m_ready = ok;
  if (ok)
    after_load();
  return ok;
}

bool BioGearsThunk::load_state_buffer(const std::string& xml)
{
  drop_requests();
  m_ready = m_engine->LoadState(xml.data(), xml.size());
  if (m_ready)
    after_load();
  return m_ready;
}

bool BioGearsThunk::save_state(const std::string& path)
{
  if (!m_ready)
    return false;
  m_engine->SaveStateToFile(path);
  return fs::exists(path);
}

std::string BioGearsThunk::save_state_to_string()
{
  if (!m_ready)
    throw std::runtime_error("engine has no state");
  const std::string tmp = temp_path("state", ".xml");
  m_engine->SaveStateToFile(tmp);
  std::ifstream in(tmp, std::ios::binary);
  if (!in)
    throw std::runtime_error("SaveStateToFile did not produce " + tmp);
  std::ostringstream ss;
  ss << in.rdbuf();
  in.close();
  std::remove(tmp.c_str());
  return ss.str();
}

bool BioGearsThunk::initialize_engine(const std::string& patient_file)
{
  drop_requests();
  m_ready = m_engine->InitializeEngine(patient_file);
  if (m_ready)
    after_load();
  return m_ready;
}

// --- data requests ---------------------------------------------------------
std::vector<bool> BioGearsThunk::set_data_requests(const std::vector<DataRequest>& requests)
{
  m_requests = requests;
  if (m_ready)
    connect_requests();
  else
    m_connected.assign(m_requests.size(), false);
  return m_connected;
}

void BioGearsThunk::connect_requests()
{
  PhysiologyEngineTrack* track = m_engine->GetEngineTrack();
  SEDataRequestManager& mgr = track->GetDataRequestManager();
  mgr.Invalidate(); // drop previous requests (the manager owns them)
  m_se_requests.clear();
  m_connected.clear();
  m_direct.clear();
  SESubstanceManager& subs = m_engine->GetSubstanceManager();

  for (const DataRequest& r : m_requests) {
    SEDataRequest* dr = nullptr;
    // Substance lookups use GetSubstance, which never creates one
    // (a Pulse lesson: name lookups that create substances).
    SESubstance* sub = r.substance.empty() ? nullptr : subs.GetSubstance(r.substance);
    bool bad_substance = !r.substance.empty() && sub == nullptr;

    if (r.category == "Physiology") {
      auto& d = mgr.CreatePhysiologyDataRequest();
      d.Set(r.property, r.unit);
      dr = &d;
    } else if (r.category == "Patient") {
      auto& d = mgr.CreatePatientDataRequest();
      d.Set(r.property, r.unit);
      dr = &d;
    } else if (r.category == "Environment") {
      auto& d = mgr.CreateEnvironmentDataRequest();
      d.Set(r.property, r.unit);
      dr = &d;
    } else if (r.category == "Equipment") {
      auto& d = mgr.CreateEquipmentDataRequest();
      d.Set(r.property, r.unit);
      dr = &d;
    } else if (r.category == "Substance") {
      if (sub) {
        auto& d = mgr.CreateSubstanceDataRequest();
        // SESubstanceDataRequest::Set(sub, name, unit) is ambiguous with the
        // (sub, cmpt, name, unit = "") overload, so set the parts separately.
        d.SetSubstance(sub);
        if (!r.compartment.empty())
          d.SetCompartment(r.compartment);
        static_cast<SEDataRequest&>(d).Set(r.property, r.unit);
        dr = &d;
      }
    } else if (r.category == "LiquidCompartment" || r.category == "GasCompartment") {
      if (!bad_substance) {
        SECompartmentSubstanceDataRequest* d = (r.category == "LiquidCompartment")
          ? static_cast<SECompartmentSubstanceDataRequest*>(&mgr.CreateLiquidCompartmentDataRequest())
          : static_cast<SECompartmentSubstanceDataRequest*>(&mgr.CreateGasCompartmentDataRequest());
        if (sub)
          d->Set(r.compartment, *sub, r.property, r.unit);
        else
          static_cast<SECompartmentDataRequest*>(d)->Set(r.compartment, r.property, r.unit);
        dr = d;
      }
    } else if (r.category == "TissueCompartment") {
      auto& d = mgr.CreateTissueCompartmentDataRequest();
      static_cast<SECompartmentDataRequest&>(d).Set(r.compartment, r.property, r.unit);
      dr = &d;
    } else if (r.category == "ThermalCompartment") {
      auto& d = mgr.CreateThermalCompartmentDataRequest();
      static_cast<SECompartmentDataRequest&>(d).Set(r.compartment, r.property, r.unit);
      dr = &d;
    } else if (r.category == "FluidPath" || r.category == "FluidNode") {
      m_direct.push_back(circuit_reader(r));
      m_se_requests.push_back(nullptr);
      m_connected.push_back(static_cast<bool>(m_direct.back()));
      continue;
    } else {
      throw std::invalid_argument("unknown data request category: " + r.category);
    }

    bool ok = false;
    if (dr) {
      try {
        ok = track->TrackRequest(*dr);
        SEDataRequestScalar* ds = track->GetScalar(dr);
        ok = ok && ds != nullptr && ds->HasScalar();
      } catch (const std::exception& e) { // e.g. "mmHg is not a valid Volume unit"
        m_log_forward->Error((std::string("pybiogears: request ") + r.property + ": " + e.what()).c_str());
        ok = false;
      }
    }
    m_se_requests.push_back(dr);
    m_connected.push_back(ok);
    m_direct.emplace_back();
  }
}

// Returns an empty function if the element, property or unit is unknown.
std::function<double()> BioGearsThunk::circuit_reader(const DataRequest& r)
{
  auto* bg = dynamic_cast<BioGears*>(m_engine.get());
  if (bg == nullptr)
    return {};
  BioGearsCircuits& circuits = bg->GetCircuits();
  const double nan = std::numeric_limits<double>::quiet_NaN();

  // Bind a scalar getter and a default unit, then validate the unit once.
  std::function<const SEScalar*()> get;
  std::string unit = r.unit;
  if (r.category == "FluidPath") {
    SEFluidCircuitPath* p = circuits.GetFluidPath(r.compartment);
    if (p == nullptr)
      return {};
    if (r.property == "Flow") {
      get = [p]() -> const SEScalar* { return p->HasFlow() ? &p->GetFlow() : nullptr; };
      if (unit.empty()) unit = "mL/s";
    } else if (r.property == "FlowSource") {
      get = [p]() -> const SEScalar* { return p->HasFlowSource() ? &p->GetFlowSource() : nullptr; };
      if (unit.empty()) unit = "mL/s";
    } else if (r.property == "PressureSource") {
      get = [p]() -> const SEScalar* { return p->HasPressureSource() ? &p->GetPressureSource() : nullptr; };
      if (unit.empty()) unit = "mmHg";
    } else if (r.property == "Resistance") {
      get = [p]() -> const SEScalar* { return p->HasResistance() ? &p->GetResistance() : nullptr; };
      if (unit.empty()) unit = "mmHg s/mL";
    } else if (r.property == "Compliance") {
      get = [p]() -> const SEScalar* { return p->HasCompliance() ? &p->GetCompliance() : nullptr; };
      if (unit.empty()) unit = "mL/mmHg";
    } else {
      return {};
    }
  } else {
    SEFluidCircuitNode* n = circuits.GetFluidNode(r.compartment);
    if (n == nullptr)
      return {};
    if (r.property == "Pressure") {
      get = [n]() -> const SEScalar* { return n->HasPressure() ? &n->GetPressure() : nullptr; };
      if (unit.empty()) unit = "mmHg";
    } else if (r.property == "Volume") {
      get = [n]() -> const SEScalar* { return n->HasVolume() ? &n->GetVolume() : nullptr; };
      if (unit.empty()) unit = "mL";
    } else {
      return {};
    }
  }
  // Check the unit is compatible now, so a bad unit is reported as unconnected.
  const SEScalar* s0 = get();
  if (s0 != nullptr && s0->IsValid()) {
    auto* q = dynamic_cast<const SEUnitScalar*>(s0);
    try {
      if (q == nullptr || q->GetCompoundUnit(unit) == nullptr)
        return {};
    } catch (const std::exception&) {
      return {};
    }
  }
  return [get, unit, nan]() {
    const SEScalar* s = get();
    if (s == nullptr || !s->IsValid())
      return nan;
    try {
      return dynamic_cast<const SEUnitScalar*>(s)->GetValue(unit);
    } catch (const std::exception&) { // incompatible unit (not checkable while the scalar was unset)
      return nan;
    }
  };
}

std::vector<double> BioGearsThunk::pull_data()
{
  const double nan = std::numeric_limits<double>::quiet_NaN();
  std::vector<double> out;
  out.reserve(m_se_requests.size() + 1);
  out.push_back(m_ready ? m_engine->GetSimulationTime(TimeUnit::s) : nan);
  PhysiologyEngineTrack* track = m_engine->GetEngineTrack();
  for (size_t i = 0; i < m_se_requests.size(); ++i) {
    if (m_ready && m_connected[i] && m_direct[i]) {
      out.push_back(m_direct[i]());
      continue;
    }
    SEDataRequest* dr = m_se_requests[i];
    if (!m_ready || !m_connected[i] || dr == nullptr) {
      out.push_back(nan);
      continue;
    }
    // Mirrors PhysiologyEngineTrack::PullData without writing to its CSV stream.
    SEDataRequestScalar* ds = track->GetScalar(dr);
    if (ds == nullptr || !ds->HasScalar()) {
      out.push_back(nan);
      continue;
    }
    ds->UpdateScalar();
    if (ds->IsValid()) {
      if (ds->HasUnit()) {
        if (dr->GetUnit() == nullptr) {
          // BioGears applies the requested unit only if the scalar already had
          // a value when the request was connected; otherwise it falls back to
          // the scalar's own unit. Apply the requested unit here instead.
          if (dr->HasRequestedUnit()) {
            const CCompoundUnit* u = nullptr;
            try {
              u = ds->GetCompoundUnit(dr->GetRequestedUnit());
            } catch (const std::exception&) {
            }
            if (u == nullptr) { // incompatible unit
              out.push_back(nan);
              continue;
            }
            dr->SetUnit(*u);
          } else {
            dr->SetUnit(*ds->GetUnit());
          }
        }
        out.push_back(ds->GetValue(dr->GetUnit()->GetString()));
      } else {
        out.push_back(ds->GetValue());
      }
    } else if (ds->IsInfinity()) {
      out.push_back(std::numeric_limits<double>::infinity());
    } else {
      out.push_back(nan);
    }
  }
  return out;
}

std::vector<std::string> BioGearsThunk::request_headings() const
{
  std::vector<std::string> out;
  PhysiologyEngineTrack* track = m_engine->GetEngineTrack();
  for (size_t i = 0; i < m_se_requests.size(); ++i) {
    SEDataRequest* dr = m_se_requests[i];
    if (dr == nullptr) { // circuit request
      const DataRequest& r = m_requests[i];
      out.push_back(r.compartment + "-" + r.property + (r.unit.empty() ? "" : "(" + r.unit + ")"));
      continue;
    }
    SEDataRequestScalar* ds = track->GetScalar(dr);
    out.push_back(ds ? ds->Heading : std::string());
  }
  return out;
}

// --- time and actions ------------------------------------------------------
bool BioGearsThunk::advance(double seconds)
{
  if (!m_ready)
    return false;
  return m_engine->AdvanceModelTime(seconds, TimeUnit::s, false);
}

// Exactly n engine time steps. advance(seconds) goes through
// AdvanceModelTime(time, unit), which carries a floating-point step remainder
// between calls, so the step count for a given interval depends on history.
bool BioGearsThunk::advance_steps(long n)
{
  if (!m_ready)
    return false;
  for (long i = 0; i < n; ++i)
    if (!m_engine->AdvanceModelTime(false))
      return false;
  return true;
}

bool BioGearsThunk::irreversible_state() const
{
  return m_ready && m_engine->GetPatient().IsEventActive(SEPatientEventType::IrreversibleState);
}

double BioGearsThunk::sim_time_s() const { return m_engine->GetSimulationTime(TimeUnit::s); }
double BioGearsThunk::time_step_s() const { return m_engine->GetTimeStep(TimeUnit::s); }

bool BioGearsThunk::process_actions(const std::string& actions_xml)
{
  if (!m_ready)
    return false;
  const std::string tmp = temp_path("actions", ".xml");
  {
    std::ofstream out(tmp);
    out << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
           "<Scenario xmlns=\"uri:/mil/tatrc/physiology/datamodel\" "
           "xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\" "
           "contentVersion=\"BioGears_8.2.0\">\n"
           "  <Name>pybiogears</Name>\n"
           "  <Description>pybiogears action batch</Description>\n"
           "  <EngineStateFile>unused</EngineStateFile>\n"
           "  <Actions>\n"
        << actions_xml << "\n  </Actions>\n</Scenario>\n";
  }
  SEScenario scenario(m_engine->GetSubstanceManager());
  bool ok = false;
  try {
    ok = scenario.Load(tmp);
  } catch (const std::exception& e) { // e.g. an unknown substance compound
    m_log_forward->Error((std::string("pybiogears: actions could not be read: ") + e.what()).c_str());
    ok = false;
  }
  std::remove(tmp.c_str());
  if (!ok)
    return false;
  for (const SEAction* a : scenario.GetActions()) {
    // BioGears 8.2.0 accepts a hemorrhage on an unknown compartment ("Could not
    // find compartment, defaulting to Aorta") but keeps the name, and on the next
    // step Cardiovascular::Hemorrhage dereferences the missing "<cmpt>Bleed" path:
    // a segfault that takes the Python process down (probe P7). Refuse it here.
    if (auto* hem = dynamic_cast<const SEHemorrhage*>(a)) {
      auto* bg = dynamic_cast<BioGears*>(m_engine.get());
      if (bg && bg->GetCircuits().GetActiveCardiovascularCircuit().GetPath(hem->GetCompartment() + "Bleed") == nullptr) {
        m_log_forward->Error(("pybiogears: hemorrhage refused, compartment has no bleed path: " + hem->GetCompartment()).c_str());
        ok = false;
        continue;
      }
    }
    try {
      if (!m_engine->ProcessAction(*a))
        ok = false;
    } catch (const std::exception& e) {
      m_log_forward->Error((std::string("pybiogears: ProcessAction threw: ") + e.what()).c_str());
      ok = false;
    }
  }
  return ok;
}

// --- logging and events ----------------------------------------------------
std::vector<LogMessage> BioGearsThunk::pull_log_messages() { return m_log_forward->drain(); }
std::vector<EventChange> BioGearsThunk::pull_events() { return m_event_handler->drain(); }
// Logger::LogToConsole only stores a flag that BioGears 8.2.0 never reads; the
// console stream is filtered by level alone, and there is no public way to
// remove it. Off = console level eFatal (the lowest), so only Fatal messages
// still reach stdout; every message still reaches the forwarder.
void BioGearsThunk::set_log_to_console(bool on)
{
  m_engine->GetLogger()->LogToConsole(on);
  m_engine->GetLogger()->SetConsoleLogLevel(on ? Logger::eInfo : Logger::eFatal);
}

} // namespace pybiogears
