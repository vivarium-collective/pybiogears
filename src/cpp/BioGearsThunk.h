// pybiogears: a thin C++ "thunk" over the BioGears engine, in the spirit of
// Pulse's PhysiologyEngineThunk. Everything crossing to Python is a plain type
// (strings, doubles, vectors of simple structs). Only BioGears' *installed*
// public headers are used (probe question for decision D2).
//
// Licensed under the Apache License, Version 2.0.
#pragma once

#include <functional>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace biogears {
class PhysiologyEngine;
class SEDataRequest;
class SEThermalApplication;
class Logger;
}

namespace pybiogears {

// One data request. `category` is one of:
//   Physiology, Patient, Environment, Equipment, Substance,
//   LiquidCompartment, GasCompartment, TissueCompartment, ThermalCompartment
//   FluidPath, FluidNode  (circuit elements of the active cardiovascular circuit;
//                          `compartment` holds the path or node name)
// `compartment` and `substance` are used only by the categories that need them.
// FluidPath properties: Flow, FlowSource, PressureSource, Resistance, Compliance.
// FluidNode properties: Pressure, Volume.
// These read engine circuit scalars directly (public BioGears::GetCircuits()),
// for quantities that have no compartment or system request, e.g. lymph flow:
// BioGears 8.2.0 creates no compartment links for lymph.
struct DataRequest {
  std::string category;
  std::string property;
  std::string unit;
  std::string compartment;
  std::string substance;
};

struct LogMessage {
  std::string level; // Debug, Info, Warning, Error, Fatal
  std::string text;
};

struct EventChange {
  std::string event;
  bool active;
  double sim_time_s;
};

class ThunkLogForward;
class ThunkEventHandler;

class BioGearsThunk {
public:
  // working_dir must contain BioGears' runtime data (xsd/, substances/,
  // patients/, states/, ...). log_file is relative to working_dir; empty = no file.
  BioGearsThunk(const std::string& working_dir, const std::string& log_file);
  ~BioGearsThunk();

  BioGearsThunk(const BioGearsThunk&) = delete;
  BioGearsThunk& operator=(const BioGearsThunk&) = delete;

  // --- state ---------------------------------------------------------------
  // sim_time_s < 0 keeps the state's own simulation time.
  bool load_state(const std::string& path, double sim_time_s = -1.0);
  bool load_state_buffer(const std::string& xml);
  bool save_state(const std::string& path);
  // BioGears has no save-to-string, so this goes through a temporary file.
  std::string save_state_to_string();
  bool initialize_engine(const std::string& patient_file, const std::string& conditions_xml = "");

  // --- data requests -------------------------------------------------------
  // Replaces all requests. Returns, for each request, whether the engine could
  // connect it to a scalar. Requests that fail are kept and read back as NaN.
  std::vector<bool> set_data_requests(const std::vector<DataRequest>& requests);
  // [sim_time_s, value_0, value_1, ...] in the order given to set_data_requests.
  std::vector<double> pull_data();
  std::vector<std::string> request_headings() const;

  // --- time and actions ----------------------------------------------------
  bool advance(double seconds);
  bool advance_steps(long n); // exactly n engine time steps
  double sim_time_s() const;
  bool irreversible_state() const; // patient IrreversibleState event active
  double time_step_s() const;
  // `actions_xml` is one or more BioGears scenario <Action> elements, e.g.
  //   <Action xsi:type="HemorrhageData" ...>...</Action>
  // They are wrapped in a minimal <Scenario> document, parsed by BioGears' own
  // public SEScenario::Load, and each one is passed to ProcessAction.
  bool process_actions(const std::string& actions_xml);
  void prune_thermal_application(const biogears::SEThermalApplication& sent);

  // --- logging and events --------------------------------------------------
  std::vector<LogMessage> pull_log_messages();
  std::vector<EventChange> pull_events();
  void set_log_to_console(bool on);

  static std::string version();
  const std::string& working_dir() const { return m_working_dir; }

private:
  void connect_requests();
  void drop_requests();
  void dedupe_compound_components();
  void after_load();
  std::function<double()> circuit_reader(const DataRequest& r);
  std::string temp_path(const std::string& stem, const std::string& ext) const;

  std::string m_working_dir;
  std::unique_ptr<ThunkLogForward> m_log_forward;
  std::unique_ptr<ThunkEventHandler> m_event_handler;
  std::unique_ptr<biogears::PhysiologyEngine> m_engine;
  std::vector<DataRequest> m_requests;
  std::vector<biogears::SEDataRequest*> m_se_requests; // owned by the engine's request manager
  std::vector<bool> m_connected;
  std::vector<std::function<double()>> m_direct; // circuit readers; empty for SE data requests
  bool m_ready = false;
  unsigned m_tmp_counter = 0;
};

} // namespace pybiogears
