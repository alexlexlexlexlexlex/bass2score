# Builds a fully self-contained Windows .exe: ffmpeg and lilypond are
# bundled INSIDE it, so whoever double-clicks it installs nothing.
#
# Must run on Windows (PyInstaller doesn't cross-compile). If you don't
# have a Windows machine, push this repo and let
# .github/workflows/build.yml build it on a GitHub-hosted Windows runner
# instead -- see README.md.
#
# Run from PowerShell (with the project venv active):  .\build_windows_app.ps1
#
# System dependencies (ffmpeg, lilypond) are resolved in this order:
#   1. already on PATH  ->  use those
#   2. Chocolatey present  ->  choco install
#   3. otherwise  ->  download the official portable builds (no admin needed)

$ErrorActionPreference = "Stop"
$AppName = "Basse2Partition"

$LilypondVersion = "2.24.4"
$FfmpegUrl   = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip"
$LilypondUrl = "https://gitlab.com/lilypond/lilypond/-/releases/v$LilypondVersion/downloads/lilypond-$LilypondVersion-mingw-x86_64.zip"

# --- 1. Locate (or fetch) ffmpeg + lilypond --------------------------------
Remove-Item -Recurse -Force bin, build_deps -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path bin | Out-Null

function Expand-Download($url, $zipPath, $destDir) {
    Write-Host "Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $destDir -Force
}

# ffmpeg / ffprobe
$ffmpegCmd  = Get-Command ffmpeg  -ErrorAction SilentlyContinue
$ffprobeCmd = Get-Command ffprobe -ErrorAction SilentlyContinue
if ($ffmpegCmd -and $ffprobeCmd) {
    $ffmpegPath  = $ffmpegCmd.Source
    $ffprobePath = $ffprobeCmd.Source
} elseif (Get-Command choco -ErrorAction SilentlyContinue) {
    choco install -y ffmpeg
    $ffmpegPath  = (Get-Command ffmpeg).Source
    $ffprobePath = (Get-Command ffprobe).Source
} else {
    New-Item -ItemType Directory -Path build_deps -Force | Out-Null
    Expand-Download $FfmpegUrl "build_deps\ffmpeg.zip" "build_deps\ffmpeg"
    $ffmpegPath  = (Get-ChildItem "build_deps\ffmpeg" -Recurse -Filter ffmpeg.exe  | Select-Object -First 1).FullName
    $ffprobePath = (Get-ChildItem "build_deps\ffmpeg" -Recurse -Filter ffprobe.exe | Select-Object -First 1).FullName
}
Copy-Item $ffmpegPath  "bin\ffmpeg.exe"
Copy-Item $ffprobePath "bin\ffprobe.exe"

# LilyPond -- the binary locates share/lilypond relative to its own path,
# so the WHOLE install tree has to travel with it (fonts, .scm files,
# bundled Guile/Python). We keep it under bin\lilypond-dist and let
# pipeline\bundled_bins.py find bin\lilypond-dist\bin\lilypond.exe.
$lilypondCmd = Get-Command lilypond -ErrorAction SilentlyContinue
if ($lilypondCmd) {
    # .../<root>/bin/lilypond.exe  ->  <root>
    $lilypondRoot = Split-Path (Split-Path $lilypondCmd.Source -Parent) -Parent
} elseif (Get-Command choco -ErrorAction SilentlyContinue) {
    choco install -y lilypond
    $lilypondExe = (Get-Command lilypond).Source
    $lilypondRoot = Split-Path (Split-Path $lilypondExe -Parent) -Parent
} else {
    New-Item -ItemType Directory -Path build_deps -Force | Out-Null
    Expand-Download $LilypondUrl "build_deps\lilypond.zip" "build_deps\lilypond"
    $lilypondRoot = (Get-ChildItem "build_deps\lilypond" -Recurse -Filter lilypond.exe | Select-Object -First 1).Directory.Parent.FullName
}
Copy-Item -Recurse $lilypondRoot "bin\lilypond-dist"
if (-not (Test-Path "bin\lilypond-dist\bin\lilypond.exe")) {
    Write-Error "LilyPond layout unexpected: bin\lilypond-dist\bin\lilypond.exe not found after copy from '$lilypondRoot'."
    exit 1
}

# --- 2. Python deps + PyInstaller -----------------------------------------
python -m pip install -r requirements.txt
python -m pip install --no-deps spleeter==2.1.0
python -m pip install --no-deps basic-pitch==0.4.0
python -m pip install pyinstaller

$basicPitchModels = python -c "import basic_pitch, os; print(os.path.join(os.path.dirname(basic_pitch.__file__), 'saved_models'))"

python -m PyInstaller `
  --name $AppName `
  --windowed `
  --noconfirm `
  --add-data "pipeline;pipeline" `
  --add-data "$basicPitchModels;basic_pitch/saved_models" `
  --add-binary "bin\ffmpeg.exe;bin" `
  --add-binary "bin\ffprobe.exe;bin" `
  --add-data "bin\lilypond-dist;bin\lilypond-dist" `
  --collect-all tensorflow `
  --collect-all onnxruntime `
  --collect-all music21 `
  --collect-all librosa `
  --collect-all tkinterdnd2 `
  --collect-all spleeter `
  --collect-all basic_pitch `
  --collect-data pretty_midi `
  --collect-data soundfile `
  app.py

Write-Host ""
Write-Host "Done -> dist\$AppName\$AppName.exe"
Write-Host "This build is self-contained: ffmpeg and lilypond are bundled inside it."
