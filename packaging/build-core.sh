#!/usr/bin/env bash
# Bundle the pure-stdlib nonya Python core into a self-contained binary so the
# end-user Mac needs no system python3. Runs in an isolated venv (does not touch
# your global packages). Output: build/dist/nonya
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
VENV="$BUILD/venv"
mkdir -p "$BUILD"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip pyinstaller >/dev/null

# Build a universal2 (arm64+x86_64) binary when the base Python is universal2;
# else fall back to single-arch with a clear warning (Intel Macs won't run it).
ARCH_FLAG=""
PYBIN="$("$VENV/bin/python" -c 'import sys; print(sys.executable)')"
if lipo -archs "$PYBIN" 2>/dev/null | grep -q x86_64 && lipo -archs "$PYBIN" 2>/dev/null | grep -q arm64; then
  ARCH_FLAG="--target-arch universal2"
else
  echo "WARN: base python is $(lipo -archs "$PYBIN" 2>/dev/null || echo single-arch) — core will NOT run on Intel. Use a universal2 python3 for a shippable build." >&2
fi
"$VENV/bin/pyinstaller" --onefile --name nonya --console $ARCH_FLAG \
  --paths "$ROOT" \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD/spec" \
  -y "$ROOT/packaging/entry.py" >/dev/null 2>&1
echo "built: $BUILD/dist/nonya ($(lipo -archs "$BUILD/dist/nonya" 2>/dev/null))"
"$BUILD/dist/nonya" --version
