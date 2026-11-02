# P1: build BioGears 8.2.0 and link from an outside project

**Date:** 2026-11-02. **Result: pass on both platforms.**

- Linux x86_64, Ubuntu 24.04, GCC 13.3, Ninja, 2 cores, 8 GB.
- macOS arm64 (Apple Silicon), AppleClang 21, Homebrew (xerces-c, xsd, eigen; Boost 1.92 present but disabled), Python 3.14 venv, 12 jobs. `scripts/install_biogears.sh --with-pybiogears`: 5 tests pass. Log: `macos-build.log` (last, incremental run).

## Recipe

Now automated by `scripts/install_biogears.sh` (BioGears unmodified; the workarounds below are applied after `cmake --install`). The manual steps it runs:

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
- Runtime data (xsd, substances, patients, 19 states incl. `StandardMale@0s.xml`) is staged into `build/runtime/`, not into the install prefix; the script assembles it at `<prefix>/share/biogears/runtime` from `share/data`, `share/xsd`, `share/Scenarios` and `share/etc/UCEDefs.conf`. The shipped states say `contentVersion="Biogears_8.1.0+74"` and load fine in 8.2.0.

## `find_package(Biogears)` from an outside project: works, with four 8.2.0 install defects

| # | Defect | Effect | Workaround in pybiogears |
|---|---|---|---|
| 1 | No `BiogearsConfigVersion.cmake` is installed. | `find_package(Biogears 8.2)` fails ("version: unknown"). | Request no version; check `biogears_version()` at run time. |
| 2 | The XSD-generated headers (`biogears/schema/cdm/*.hxx`) are not installed, but installed public headers include them (e.g. `SEElectricalCircuit.h` includes `biogears/schema/cdm/Circuit.hxx`). | Outside builds fail to compile. | The install script copies `<build>/projects/biogears/generated/Release/biogears/schema` into `<prefix>/include/biogears/`. They are the headers the libraries were compiled from, so they match. (Fallback: `BIOGEARS_SCHEMA_INCLUDE_DIR`.) |
| 3 | Installed `libbiogears.so` and `bin/*` have no RUNPATH: BioGears sets `INSTALL_RPATH` per target with a malformed generator expression (`$<UNIX AND NOT APPLE:...>`), which also overrides `CMAKE_INSTALL_RPATH`. | `bg-scenario` needs `LD_LIBRARY_PATH`; a module linking only `libbiogears` can't find `libbiogears_cdm` through a modern RUNPATH. | pybiogears links with `--disable-new-dtags` (transitive DT_RPATH); the script writes `bg-scenario.sh`/`bg-cli.sh` wrappers. |
| 4 | Public headers need C++20 (`concept`, `requires` in `cdm/Macros.h`). | C++17 consumers fail. | `CMAKE_CXX_STANDARD 20`. |

Defect 2 is the one that bears on **D2**: everything the thunk needs is in the public headers, but installing them is incomplete. With the copy step in the install script, no upstream change and no fork is needed (D2 settled; D4 optional, a courtesy bug report).

## Other API facts found while writing the thunk

- `io::Actions` (the action XML serializer) is `BIOGEARS_PRIVATE_API`. The public route for actions-as-XML is `SEScenario::Load(file)`, then `ProcessAction` on each action; the thunk wraps actions in a minimal `<Scenario>` in a temporary file.
- `SESubstanceDataRequest::Set(sub, name, unit)` with `std::string` arguments is ambiguous with `Set(sub, cmpt, name, unit="")`; set the parts separately.
- `Logger::Fatal` logs and does not throw; an unknown data-request name is logged as Fatal ("Unknown Data Request") and the request reads NaN.

## macOS: three more build issues, all handled in the install script

BioGears unmodified in each case.

| # | Issue | Workaround |
|---|---|---|
| M1 | The bundled zlib 1.2.11 (`projects/zip/lib/zlib`) does `#define fdopen(fd,mode) NULL` when `TARGET_OS_MAC` is defined. Current macOS SDKs define it, so the macro breaks `<stdio.h>` (fixed in zlib 1.2.12). | `-DCMAKE_C_FLAGS=-Dfdopen=fdopen` (zutil.h skips its define when `fdopen` is already a macro). |
| M2 | bg-cli/bg-scenario include Boost.Process v1 headers (`boost/process/exception.hpp`), absent from Homebrew Boost 1.92. | `-DCMAKE_DISABLE_FIND_PACKAGE_Boost=ON`; the tools build with their own argument parser (keyword syntax: `bg-scenario SCENARIO file.xml`). |
| M3 | The malformed `INSTALL_RPATH` (defect 3) makes `install_name_tool` fail during `cmake --install` ("more than one input file specified"); installed dylibs and tools keep build-tree load paths. | After install, rewrite BioGears dependencies to `@rpath/<name>`, set install ids to `@rpath/<name>`, replace rpaths with `@loader_path` (libs) or `@loader_path/lib` (tools), re-sign ad hoc; fail if any build-tree path remains. |

Other macOS differences:
- Defect 2 does not occur: on macOS BioGears generates into `generated/` (not `generated/<CONFIG>/`), and its install rule copies the schema headers. The script skips its copy step when the headers are present.
- The tools install to the prefix root, not `bin/`; the wrappers in `bin/` point at them.
- The cold-build time was not captured: the log was overwritten by the two incremental re-runs after M1 and M2.

## Open

- P2 oracle comparison on macOS (bg-scenario vs binding). The Linux result is a 4e-13 match; on macOS only the smoke tests have run.
