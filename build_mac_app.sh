#!/bin/bash
# Builds a fully self-contained macOS .app: ffmpeg and lilypond are bundled
# INSIDE the app, so whoever double-clicks it needs to install nothing.
#
# This must run on a Mac (PyInstaller doesn't cross-compile). If you don't
# have one, push this repo and let .github/workflows/build.yml do it on a
# GitHub-hosted Mac runner instead -- see README.md.
set -euo pipefail

APP_NAME="Basse2Partition"

# --- 1. Get ffmpeg/lilypond onto this build machine (only needed to BUILD
#        the app, not needed by whoever runs the final .app) --------------
if ! command -v ffmpeg >/dev/null || ! command -v lilypond >/dev/null; then
  echo "Installing build-time dependencies via Homebrew..."
  brew install ffmpeg lilypond
fi

# --- 2. Copy the actual binaries (not symlinks) into ./bin so PyInstaller
#        can bundle them ---------------------------------------------------
rm -rf bin && mkdir bin
cp "$(command -v ffmpeg)" bin/ffmpeg
cp "$(command -v ffprobe)" bin/ffprobe
# lilypond is usually a wrapper script pointing into a Cellar/versions tree
# with its own lib/fonts alongside -- copy the whole real directory, not
# just the binary, or font/glyph lookups will fail at runtime.
LILYPOND_REAL=$(readlink -f "$(command -v lilypond)")
LILYPOND_PREFIX=$(dirname "$(dirname "$LILYPOND_REAL")")  # .../lilypond/<version>
cp -R "$LILYPOND_PREFIX" bin/lilypond-dist
cat > bin/lilypond <<'INNEREOF'
#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/lilypond-dist/bin/lilypond" "$@"
INNEREOF
chmod +x bin/lilypond bin/ffmpeg bin/ffprobe

# --- 3. Python deps + PyInstaller ------------------------------------------
pip install --quiet -r requirements.txt
pip install --quiet --no-deps spleeter==2.1.0
pip install --quiet --no-deps basic-pitch==0.4.0
pip install --quiet pyinstaller

BASIC_PITCH_MODELS=$(python3 -c "import basic_pitch, os; print(os.path.join(os.path.dirname(basic_pitch.__file__), 'saved_models'))")

pyinstaller \
  --name "$APP_NAME" \
  --windowed \
  --noconfirm \
  --add-data "pipeline:pipeline" \
  --add-data "$BASIC_PITCH_MODELS:basic_pitch/saved_models" \
  --add-binary "bin/ffmpeg:bin" \
  --add-binary "bin/ffprobe:bin" \
  --add-binary "bin/lilypond:bin" \
  --add-data "bin/lilypond-dist:bin/lilypond-dist" \
  --collect-all tensorflow \
  --collect-all onnxruntime \
  --collect-all music21 \
  --collect-all librosa \
  --collect-all tkinterdnd2 \
  --collect-all spleeter \
  --collect-all basic_pitch \
  --collect-data pretty_midi \
  --collect-data soundfile \
  app.py

# PyInstaller resets file mtimes, making LilyPond's *.scm sources look newer
# than their compiled *.go bytecode; it then recompiles at runtime, which is
# slow and can crash Guile. Bump every .go mtime so the bytecode is used
# as-is.
LY_DIST="dist/${APP_NAME}.app/Contents/Frameworks/bin/lilypond-dist"
[ -d "$LY_DIST" ] || LY_DIST="dist/${APP_NAME}.app/Contents/Resources/bin/lilypond-dist"
if [ -d "$LY_DIST" ]; then
  find "$LY_DIST" -name '*.go' -exec touch {} +
  echo "Refreshed LilyPond .go bytecode timestamps."
fi

echo ""
echo "Done -> dist/${APP_NAME}.app"
echo "This .app is self-contained: ffmpeg and lilypond are bundled inside it."
echo "Anyone can now double-click it without installing anything else."
