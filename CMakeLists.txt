cmake_minimum_required(VERSION 3.20)
project(pybiogears LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20) # BioGears 8.2.0 headers use C++20 concepts
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# BioGears must be installed (cmake --install) and findable, e.g.
#   -DCMAKE_PREFIX_PATH=/path/to/biogears/install
# BiogearsConfig.cmake ships FindCodeSynthesis.cmake next to itself.
# 8.2.0 installs no BiogearsConfigVersion.cmake, so no version can be requested here;
# the version is checked at import (pybiogears.biogears_version()).
find_package(Biogears REQUIRED)
find_package(pybind11 CONFIG REQUIRED)

# src/cpp/biogears_data: BioGears' own CSV-to-XML generators (bg-cli's
# projects/cli/src/data, copied unmodified; see its README), behind generate_data.cpp.
#
# They build BioGears' XSD-generated data objects here and serialize them in
# libbiogears_cdm, which looks each object's type up by its C++ type_info. pybind11
# compiles a module with hidden visibility, which on macOS gives the module private
# copies of template type_info that never match libbiogears_cdm's (libc++ compares
# type_info by address there; libstdc++ on Linux compares names), so every
# serialization throws. These sources are therefore compiled separately, with default
# visibility, so their type_info is shared with BioGears' libraries.
file(GLOB BIOGEARS_DATA_SOURCES CONFIGURE_DEPENDS src/cpp/biogears_data/*.cpp)
add_library(biogears_data OBJECT src/cpp/generate_data.cpp ${BIOGEARS_DATA_SOURCES})
set_target_properties(biogears_data PROPERTIES
  POSITION_INDEPENDENT_CODE ON
  CXX_VISIBILITY_PRESET default
  VISIBILITY_INLINES_HIDDEN OFF)
target_link_libraries(biogears_data PRIVATE Biogears::libbiogears Biogears::libbiogears_cdm
                      Biogears::libbiogears_common)

pybind11_add_module(_pybiogears
  src/cpp/BioGearsThunk.cpp
  $<TARGET_OBJECTS:biogears_data>
  src/cpp/bindings.cpp)
target_link_libraries(_pybiogears PRIVATE Biogears::libbiogears Biogears::libbiogears_cdm
                      Biogears::libbiogears_common)

# BioGears 8.2.0's install omits the XSD-generated headers (biogears/schema/...)
# that its public headers include (probe P1). Until that is fixed upstream,
# point at the BioGears build tree's generated directory, e.g.
#   <build>/projects/biogears/generated/Release
set(BIOGEARS_SCHEMA_INCLUDE_DIR "" CACHE PATH "Directory holding biogears/schema/*.hxx")
if(NOT BIOGEARS_SCHEMA_INCLUDE_DIR AND DEFINED ENV{BIOGEARS_SCHEMA_INCLUDE_DIR})
  set(BIOGEARS_SCHEMA_INCLUDE_DIR "$ENV{BIOGEARS_SCHEMA_INCLUDE_DIR}")
endif()
if(BIOGEARS_SCHEMA_INCLUDE_DIR)
  target_include_directories(_pybiogears SYSTEM PRIVATE "${BIOGEARS_SCHEMA_INCLUDE_DIR}")
  target_include_directories(biogears_data SYSTEM PRIVATE "${BIOGEARS_SCHEMA_INCLUDE_DIR}")
endif()

# Find the BioGears shared libraries at run time without LD_LIBRARY_PATH.
get_target_property(_bg_lib Biogears::libbiogears LOCATION)
get_filename_component(_bg_libdir "${_bg_lib}" DIRECTORY)
set_target_properties(_pybiogears PROPERTIES
  BUILD_RPATH "${_bg_libdir}"
  INSTALL_RPATH "${_bg_libdir}")
if(UNIX AND NOT APPLE)
  # The installed libbiogears.so has no RUNPATH of its own, so its sibling
  # libraries (libbiogears_cdm, libbiogears_io) must be found through ours.
  # Only an old-style DT_RPATH applies transitively.
  target_link_options(_pybiogears PRIVATE "LINKER:--disable-new-dtags")
endif()

install(TARGETS _pybiogears LIBRARY DESTINATION pybiogears)
