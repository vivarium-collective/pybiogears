// Runs BioGears' own CSV-to-XML data generators (src/cpp/biogears_data, copied unmodified
// from BioGears 8.2.0's bg-cli) from Python. Licensed under the Apache License, Version 2.0.
//
// The generators read `templates/<Kind>.csv` and write `<folder>/<Name>.xml` relative to
// the current working directory, and report on std::cout. generate_data() switches to
// the given directory for the run and captures std::cout, restoring both afterwards.
// Both are process-wide, so pybiogears.generate_data runs this in a child process.
#include "generate_data.h"

#include "biogears_data/CompoundGenerator.h"
#include "biogears_data/EnvironmentGenerator.h"
#include "biogears_data/NutritionGenerator.h"
#include "biogears_data/PatientGenerator.h"
#include "biogears_data/StabilizationGenerator.h"
#include "biogears_data/SubstanceGenerator.h"

#include <filesystem>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>

namespace pybiogears {

namespace {
  std::unique_ptr<biogears::CSVToXMLConvertor> make_generator(const std::string& kind)
  {
    if (kind == "substances") return std::make_unique<biogears::SubstanceGenerator>();
    if (kind == "environments") return std::make_unique<biogears::EnvironmentGenerator>();
    if (kind == "patients") return std::make_unique<biogears::PatientGenerator>();
    if (kind == "stabilizations") return std::make_unique<biogears::StabilizationGenerator>();
    if (kind == "nutrition") return std::make_unique<biogears::NutritionGenerator>();
    if (kind == "compounds") return std::make_unique<biogears::CompoundGenerator>();
    throw std::invalid_argument("unknown data kind: " + kind);
  }

  struct Restore {
    std::filesystem::path cwd;
    std::streambuf* cout;
    ~Restore()
    {
      std::cout.rdbuf(cout);
      std::error_code ec;
      std::filesystem::current_path(cwd, ec);
    }
  };
}

std::vector<GeneratedKind> generate_data(const std::string& workdir, const std::vector<std::string>& kinds)
{
  std::vector<std::unique_ptr<biogears::CSVToXMLConvertor>> generators;
  for (const auto& kind : kinds)
    generators.push_back(make_generator(kind));   // unknown kinds fail before anything runs

  std::vector<GeneratedKind> out;
  Restore restore { std::filesystem::current_path(), std::cout.rdbuf() };
  std::filesystem::current_path(workdir);
  for (std::size_t i = 0; i < kinds.size(); ++i) {
    std::ostringstream log;
    std::cout.rdbuf(log.rdbuf());
    GeneratedKind result;
    result.kind = kinds[i];
    try {
      result.parsed = generators[i]->parse();
      generators[i]->save();                       // always returns false in BioGears 8.2.0
    } catch (const std::exception& e) {
      result.parsed = false;
      log << "exception: " << e.what() << "\n";
    }
    std::cout.rdbuf(restore.cout);
    result.log = log.str();
    out.push_back(std::move(result));
  }
  return out;
}

} // namespace pybiogears
