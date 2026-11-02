#!/usr/bin/env bash
# Build and install stock BioGears 8.2.0 for pybiogears, in one command.
#
#   scripts/install_biogears.sh [--prefix DIR] [--work DIR] [--jobs N] [--with-pybiogears]
#
# Defaults: --prefix ~/biogears-8.2.0-install   --work ~/biogears-8.2.0-build
#
# BioGears itself is not modified. After `cmake --install`, the script makes the
# install self-contained (see experiments/p1/README.md):
#   - copies the XSD-generated headers (biogears/schema/...), which 8.2.0's
#     install omits although its public headers include them;
#   - assembles a runtime directory (xsd, substances, patients, states, ...) at
#     <prefix>/share/biogears/runtime, for $BIOGEARS_RUNTIME.
# Re-running is incremental: an existing clone and build directory are reused.
#
# Dependencies
#   Linux (Debian/Ubuntu): sudo apt-get install cmake ninja-build g++ git xsdcxx \
#       libxerces-c-dev libeigen3-dev libboost-dev libboost-system-dev \
#       libboost-filesystem-dev libboost-date-time-dev libboost-program-options-dev
#   macOS (Homebrew):      brew install cmake ninja git xerces-c xsd eigen
#   On Linux, Boost gives bg-cli/bg-scenario their Boost-based option parser and
#   subprocess support; on macOS it is disabled (see below). Either way the tools
#   are built; they are only needed as test oracles.
set -euo pipefail

BIOGEARS_TAG=8.2.0
BIOGEARS_COMMIT=c5227b63888a7b600087f6aeff7c5649d9b7e8f9
BIOGEARS_REPO=https://github.com/BioGearsEngine/core.git

PREFIX="$HOME/biogears-${BIOGEARS_TAG}-install"
WORK="$HOME/biogears-${BIOGEARS_TAG}-build"
JOBS=""
WITH_PYBIOGEARS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --work) WORK="$2"; shift 2 ;;
    --jobs) JOBS="$2"; shift 2 ;;
    --with-pybiogears) WITH_PYBIOGEARS=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

PREFIX="$(mkdir -p "$PREFIX" && cd "$PREFIX" && pwd)"
WORK="$(mkdir -p "$WORK" && cd "$WORK" && pwd)"
SRC="$WORK/src"
BUILD="$WORK/build"
PYBIOGEARS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "$JOBS" ]]; then
  JOBS="$( (command -v nproc >/dev/null && nproc) || sysctl -n hw.ncpu 2>/dev/null || echo 2)"
fi

# --- dependencies -------------------------------------------------------------
for tool in git cmake ninja; do
  command -v "$tool" >/dev/null || die "$tool not found (see the dependency list at the top of this script)"
done

CMAKE_EXTRA=()
XSD_EXE="$(command -v xsdcxx || command -v xsd || true)"
[[ -n "$XSD_EXE" ]] || die "CodeSynthesis XSD (xsdcxx or xsd) not found"
# Homebrew's `xsd` and Mono's `xsd` share a name; make sure this is CodeSynthesis.
"$XSD_EXE" --version 2>&1 | grep -qi codesynthesis || die "$XSD_EXE is not CodeSynthesis XSD"
CMAKE_EXTRA+=("-DCodeSynthesis_EXECUTABLE=$XSD_EXE")

if [[ "$(uname)" == "Darwin" ]]; then
  command -v brew >/dev/null || die "Homebrew not found"
  BREW="$(brew --prefix)"
  CMAKE_EXTRA+=("-DCMAKE_PREFIX_PATH=$BREW;$(brew --prefix xerces-c 2>/dev/null || echo "$BREW")")
  # BioGears bundles zlib 1.2.11 (projects/zip/lib/zlib), whose zutil.h does
  # `#define fdopen(fd,mode) NULL` when TARGET_OS_MAC is defined; recent macOS
  # SDKs define it, and the macro breaks <stdio.h> (fixed in zlib 1.2.12).
  # zutil.h skips that define if fdopen is already a macro, so make it one
  # that expands to itself. C only; BioGears' sources are not touched.
  CMAKE_EXTRA+=("-DCMAKE_C_FLAGS=-Dfdopen=fdopen")
  # BioGears' CLI uses Boost.Process v1 headers (boost/process/exception.hpp),
  # which current Homebrew Boost (1.92) no longer ships. Boost is optional for
  # BioGears; without it bg-cli/bg-scenario build with their own argument parser
  # (keyword syntax: bg-scenario SCENARIO file.xml).
  CMAKE_EXTRA+=("-DCMAKE_DISABLE_FIND_PACKAGE_Boost=ON")
fi

# --- source -------------------------------------------------------------------
if [[ ! -d "$SRC/.git" ]]; then
  say "Cloning BioGears $BIOGEARS_TAG into $SRC"
  git clone --depth 1 --branch "$BIOGEARS_TAG" "$BIOGEARS_REPO" "$SRC"
fi
HEAD="$(git -C "$SRC" rev-parse HEAD)"
[[ "$HEAD" == "$BIOGEARS_COMMIT" ]] || die "$SRC is at $HEAD, expected $BIOGEARS_TAG ($BIOGEARS_COMMIT)"
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || die "$SRC has local changes; this script builds stock $BIOGEARS_TAG"

# --- configure, build, install ------------------------------------------------
say "Configuring (build dir $BUILD)"
cmake -S "$SRC" -B "$BUILD" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DBiogears_BUILD_HOWTOS=OFF \
  -DBiogears_BUILD_TEST=OFF \
  -DBiogears_BUILD_WEBSITE=OFF \
  "${CMAKE_EXTRA[@]}"

say "Building with $JOBS jobs (a cold build takes a while: ~2000 steps)"
start=$(date +%s)
cmake --build "$BUILD" -j "$JOBS"
say "Build took $(( $(date +%s) - start )) s"

say "Installing into $PREFIX"
cmake --install "$BUILD"

# --- make the install self-contained -----------------------------------------
GEN="$BUILD/projects/biogears/generated/Release/biogears/schema"
[[ -d "$GEN" ]] || die "generated schema headers not found at $GEN"
say "Copying XSD-generated headers into $PREFIX/include/biogears/schema"
mkdir -p "$PREFIX/include/biogears"
rm -rf "$PREFIX/include/biogears/schema"
cp -R "$GEN" "$PREFIX/include/biogears/schema"

RUNTIME="$PREFIX/share/biogears/runtime"
say "Assembling runtime directory $RUNTIME"
rm -rf "$RUNTIME"
mkdir -p "$RUNTIME"
cp -R "$SRC/share/data/." "$RUNTIME/"
cp -R "$SRC/share/xsd" "$RUNTIME/xsd"
cp -R "$SRC/share/Scenarios" "$RUNTIME/Scenarios"
cp "$SRC/share/etc/UCEDefs.conf" "$RUNTIME/"
[[ -f "$RUNTIME/states/StandardMale@0s.xml" ]] || die "runtime is missing states/StandardMale@0s.xml"

# BioGears' INSTALL_RPATH generator expression is malformed in 8.2.0, so the
# installed tools can't find their libraries. Wrap the oracle tools.
# On macOS BioGears installs the tools to the prefix root instead of bin/.
mkdir -p "$PREFIX/bin"
for tool in bg-scenario bg-cli; do
  exe=""
  for cand in "$PREFIX/bin/$tool" "$PREFIX/$tool"; do
    [[ -x "$cand" && -f "$cand" ]] && exe="$cand" && break
  done
  if [[ -n "$exe" ]]; then
    cat > "$PREFIX/bin/$tool.sh" <<WRAP
#!/usr/bin/env bash
export LD_LIBRARY_PATH="$PREFIX/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export DYLD_LIBRARY_PATH="$PREFIX/lib\${DYLD_LIBRARY_PATH:+:\$DYLD_LIBRARY_PATH}"
exec "$exe" "\$@"
WRAP
    chmod +x "$PREFIX/bin/$tool.sh"
  fi
done

# --- optional: pybiogears -----------------------------------------------------
if [[ "$WITH_PYBIOGEARS" == 1 ]]; then
  say "Building pybiogears from $PYBIOGEARS_DIR"
  CMAKE_PREFIX_PATH="$PREFIX" python3 -m pip install "$PYBIOGEARS_DIR[test]"
  say "Running pybiogears tests"
  (cd "$RUNTIME" && BIOGEARS_RUNTIME="$RUNTIME" python3 -m pytest -q "$PYBIOGEARS_DIR/tests")
fi

cat <<EOF

BioGears $BIOGEARS_TAG is installed.
  prefix:   $PREFIX
  runtime:  $RUNTIME

Build pybiogears against it:
  CMAKE_PREFIX_PATH=$PREFIX pip install -e $PYBIOGEARS_DIR

and point it at the runtime:
  export BIOGEARS_RUNTIME=$RUNTIME

Oracle tools (run from \$BIOGEARS_RUNTIME): $PREFIX/bin/bg-scenario.sh, bg-cli.sh
EOF
