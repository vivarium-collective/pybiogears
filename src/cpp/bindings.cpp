// pybind11 module `pybiogears._pybiogears`. Licensed under the Apache License, Version 2.0.
#include "BioGearsThunk.h"

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
using pybiogears::BioGearsThunk;
using pybiogears::DataRequest;
using pybiogears::EventChange;
using pybiogears::LogMessage;

PYBIND11_MODULE(_pybiogears, m)
{
  m.doc() = "Low-level BioGears engine thunk. Use the pybiogears package, not this module.";

  py::class_<DataRequest>(m, "DataRequest")
    .def(py::init<>())
    .def(py::init([](std::string category, std::string property, std::string unit,
                     std::string compartment, std::string substance) {
           return DataRequest { category, property, unit, compartment, substance };
         }),
         py::arg("category"), py::arg("property"), py::arg("unit") = "",
         py::arg("compartment") = "", py::arg("substance") = "")
    .def_readwrite("category", &DataRequest::category)
    .def_readwrite("property", &DataRequest::property)
    .def_readwrite("unit", &DataRequest::unit)
    .def_readwrite("compartment", &DataRequest::compartment)
    .def_readwrite("substance", &DataRequest::substance);

  py::class_<LogMessage>(m, "LogMessage")
    .def_readonly("level", &LogMessage::level)
    .def_readonly("text", &LogMessage::text)
    .def("__repr__", [](const LogMessage& l) { return "<" + l.level + ": " + l.text + ">"; });

  py::class_<EventChange>(m, "EventChange")
    .def_readonly("event", &EventChange::event)
    .def_readonly("active", &EventChange::active)
    .def_readonly("sim_time_s", &EventChange::sim_time_s);

  py::class_<BioGearsThunk>(m, "BioGearsThunk")
    .def(py::init<const std::string&, const std::string&>(), py::arg("working_dir"), py::arg("log_file") = "")
    .def("load_state", &BioGearsThunk::load_state, py::arg("path"), py::arg("sim_time_s") = -1.0,
         py::call_guard<py::gil_scoped_release>())
    .def("load_state_buffer", [](BioGearsThunk& t, py::bytes b) {
           std::string s = b;
           py::gil_scoped_release r;
           return t.load_state_buffer(s);
         })
    .def("save_state", &BioGearsThunk::save_state, py::call_guard<py::gil_scoped_release>())
    .def("save_state_to_bytes", [](BioGearsThunk& t) {
           std::string s;
           {
             py::gil_scoped_release r;
             s = t.save_state_to_string();
           }
           return py::bytes(s);
         })
    .def("total_blood_volume_lost_mL", &BioGearsThunk::total_blood_volume_lost_mL)
    .def("set_total_blood_volume_lost_mL", &BioGearsThunk::set_total_blood_volume_lost_mL)
    .def("initialize_engine", &BioGearsThunk::initialize_engine, py::arg("patient_file"), py::arg("conditions_xml") = "",
         py::arg("config_xml") = "", py::call_guard<py::gil_scoped_release>())
    .def_property_readonly("ready", &BioGearsThunk::ready)
    .def("run_scenario", &BioGearsThunk::run_scenario, py::arg("scenario_file"), py::arg("results_file"),
         py::call_guard<py::gil_scoped_release>())
    .def("active_events", &BioGearsThunk::active_events)
    .def("set_data_requests", &BioGearsThunk::set_data_requests)
    .def("request_headings", &BioGearsThunk::request_headings)
    .def("pull_data", [](BioGearsThunk& t) {
           auto v = t.pull_data();
           return py::array_t<double>(v.size(), v.data());
         })
    .def("advance", &BioGearsThunk::advance, py::arg("seconds"), py::call_guard<py::gil_scoped_release>())
    .def("advance_steps", &BioGearsThunk::advance_steps, py::arg("n"), py::call_guard<py::gil_scoped_release>())
    .def_property_readonly("sim_time_s", &BioGearsThunk::sim_time_s)
    .def_property_readonly("irreversible_state", &BioGearsThunk::irreversible_state)
    .def_property_readonly("time_step_s", &BioGearsThunk::time_step_s)
    .def_property_readonly("working_dir", &BioGearsThunk::working_dir)
    .def("process_actions", &BioGearsThunk::process_actions, py::call_guard<py::gil_scoped_release>())
    .def("pull_log_messages", &BioGearsThunk::pull_log_messages)
    .def("pull_events", &BioGearsThunk::pull_events)
    .def("set_log_to_console", &BioGearsThunk::set_log_to_console)
    .def_static("version", &BioGearsThunk::version);
}
