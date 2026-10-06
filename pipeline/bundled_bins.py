"""
Path resolution for external binaries (ffmpeg, ffprobe, lilypond).

- Running from source (`python app.py`): use whatever is on PATH.
- Running from a PyInstaller bundle: use the copies placed in the bundle's
  `bin/` folder by the build script (build_mac_app.sh /
  build_windows_app.ps1), so end users install nothing.

This is the single place that knows about that distinction -- the rest of
the pipeline just calls bin_path("ffmpeg") etc.
"""
import os
import shutil
import sys
from pathlib import Path

_EXE_SUFFIX = ".exe" if sys.platform.startswith("win") else ""


def _bundle_root() -> Path | None:
    """Return the PyInstaller bundle root when running as a frozen app,
    else None."""
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys._MEIPASS to the temp extraction dir (--onefile)
        # or the app's Resources dir (--onedir / macOS .app bundle).
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return None


def _prepend_bundled_bins_to_path() -> None:
    """Put the bundled binaries on PATH so third-party code that looks them
    up itself finds them too -- Spleeter's audio adapter, for instance, does
    its own ``shutil.which("ffmpeg")`` and never calls ``bin_path``."""
    root = _bundle_root()
    if root is None:
        return
    extra = [root / "bin", root / "bin" / "lilypond-dist" / "bin"]
    dirs = [str(p) for p in extra if p.is_dir()]
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])


_prepend_bundled_bins_to_path()


def bin_path(name: str) -> str:
    """Resolve the path to an external binary (`ffmpeg`, `ffprobe`,
    `lilypond`). Bundled copy takes priority when frozen; falls back to
    PATH otherwise (handy for development)."""
    root = _bundle_root()
    if root is not None:
        candidates = [root / "bin" / f"{name}{_EXE_SUFFIX}"]
        if name == "lilypond":
            # On Windows the LilyPond binary must stay inside its own
            # install tree (it locates share/lilypond relative to the exe),
            # so the build keeps the whole tree under bin/lilypond-dist
            # instead of copying a bare lilypond.exe into bin/.
            candidates += [
                root / "bin" / "lilypond-dist" / "bin" / f"lilypond{_EXE_SUFFIX}",
                root / "bin" / "lilypond-dist" / "usr" / "bin" / f"lilypond{_EXE_SUFFIX}",
            ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

    found = shutil.which(name)
    if found:
        return found

    raise FileNotFoundError(
        f"Could not find '{name}'. If running from source, install it "
        f"(e.g. `brew install ffmpeg lilypond` on macOS). If running from "
        f"the packaged app, it should have been bundled by the build "
        f"script -- this indicates a packaging bug."
    )
