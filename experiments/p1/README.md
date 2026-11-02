# P1: build BioGears 8.2.0 and link from an outside project

**Date:** 2026-11-02. **Platform:** Linux x86_64, Ubuntu 24.04, GCC 13.3, CMake (system), Ninja, 2 cores, 8 GB. The macOS build is still to do (see "Open").

## Recipe

```sh
sudo apt-get install xsdcxx libxerces-c-dev libeigen3-dev \
     libboost-dev libboost-system-dev libboost-filesystem-dev \
     libboost-date-time-dev libboost-program-options-dev   # Boost: only for bg-cli/bg-scenario
git clone --depth 1 --branch 8.2.0 https://github.com/BioGearsEngine/core.git biogears-8.2.0   # c5227b63
cmake -S biogears-8.2.0 -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=$PREFIX \
      -DBiogears_BUILD_HOWTOS=OFF -DBiogears_BUILD_TEST=OFF -DBiogears_BUILD_WEBSITE=OFF \
      -DCodeSynthesis_EXECUTABLE=/usr/bin/xsdcxx
ninja -C build -j2 && cmake --install build
```

- 2038 Ninja steps. The build was run in two pieces (the first was interrupted by a reconfigure for Boost and its time was not recorded); the second piece, steps 1564-2038, took 708 s at `-j2`. Most of the time is the XSD-generated schema code (libbiogears_cdm) and the engine sources.
- Install: 75 MB (`lib/libbiogears{,_cdm,_io}.so.8.2`, `bin/bg-cli`, `bin/bg-scenario`, `include/`, `lib/cmake/Biogears/`).
- Runtime data (xsd, substances, patients, 19 states incl. `StandardMale@0s.xml`) is staged into `build/runtime/`, not into the install prefix. The shipped states say `contentVersion="Biogears_8.1.0+74"` and load fine in 8.2.0.

## `find_package(Biogears)` from an outside project: works, with four 8.2.0 install defects

| # | Defect | Effect | Workaround in pybiogears |
|---|---|---|---|
| 1 | No `BiogearsConfigVersion.cmake` is installed. | `find_package(Biogears 8.2)` fails ("version: unknown"). | Request no version; check `biogears_version()` at run time. |
| 2 | The XSD-generated headers (`biogears/schema/cdm/*.hxx`) are not installed, but installed public headers include them (e.g. `SEElectricalCircuit.h` includes `biogears/schema/cdm/Circuit.hxx`). | Outside builds fail to compile. | `BIOGEARS_SCHEMA_INCLUDE_DIR` = `<build>/projects/biogears/generated/Release`. |
| 3 | Installed `libbiogears.so` and `bin/*` have no RUNPATH. | `bg-scenario` needs `LD_LIBRARY_PATH`; a module linking only `libbiogears` can't find `libbiogears_cdm` through a modern RUNPATH. | Link with `--disable-new-dtags` (transitive DT_RPATH). |
| 4 | Public headers need C++20 (`concept`, `requires` in `cdm/Macros.h`). | C++17 consumers fail. | `CMAKE_CXX_STANDARD 20`. |

Defect 2 is the one that bears on **D2**: everything the thunk needs is in the public headers, but installing them is incomplete. It is a one-line `install(DIRECTORY ...)` fix upstream; until then, building pybiogears needs the BioGears build tree, not just the install. This argues for offering an upstream PR (D4) rather than a fork.

## Other API facts found while writing the thunk

- `io::Actions` (the action XML serializer) is `BIOGEARS_PRIVATE_API`. The public route for actions-as-XML is `SEScenario::Load(file)`, then `ProcessAction` on each action; the thunk wraps actions in a minimal `<Scenario>` in a temporary file.
- `SESubstanceDataRequest::Set(sub, name, unit)` with `std::string` arguments is ambiguous with `Set(sub, cmpt, name, unit="")`; set the parts separately.
- `Logger::Fatal` logs and does not throw; an unknown data-request name is logged as Fatal ("Unknown Data Request") and the request reads NaN.

## Open

- macOS (arm64, Homebrew xerces-c/xsd/eigen) build of 8.2.0: the user's existing Mac build is of the 2019 working tree (7.2.1) and can't be used. This must be run on the Mac itself; the session's shell on the user's computer is a Linux VM (aarch64, 4 cores, 3 GB, no CMake), which can't build for macOS.
