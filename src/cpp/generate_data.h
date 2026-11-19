// Licensed under the Apache License, Version 2.0.
#pragma once
#include <string>
#include <vector>

namespace pybiogears {

struct GeneratedKind {
  std::string kind;
  bool parsed = false;   // the generator's parse() result: false if a value didn't convert
  std::string log;       // what the generator printed (one "Saved <file>" line per file written)
};

// Runs BioGears' generators for `kinds` (substances, environments, patients,
// stabilizations, nutrition, compounds) in `workdir`, which holds templates/<Kind>.csv
// and the output folders. Changes the process's working directory and std::cout for
// the duration of the call.
std::vector<GeneratedKind> generate_data(const std::string& workdir, const std::vector<std::string>& kinds);

} // namespace pybiogears
