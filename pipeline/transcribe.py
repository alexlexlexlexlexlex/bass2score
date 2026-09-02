"""
Pipeline: audio file -> isolated instrument stem -> MIDI transcription ->
PDF + MusicXML score.

Usage (CLI):
    python transcribe.py input.mp3 --instrument bass --output-dir out/

Usage (as a library, e.g. from a backend job worker):
    from transcribe import run_pipeline
    result = run_pipeline("input.mp3", instrument="bass", output_dir="out/")
    # result = {"pdf": "out/track_bass.pdf", "midi": ..., "musicxml": ...}

Only "bass" is implemented for now (per the current product scope), but the
INSTRUMENT_PROFILES dict is the extension point for the other Spleeter
4stems outputs (vocals / drums / other) -- see the comment near its
definition.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pretty_midi as pm
import soundfile as sf

from bundled_bins import bin_path

import spleeter_compat  # noqa: F401 -- must run before importing spleeter
from model_download import ensure_model
from spleeter.separator import Separator

SR = 44100


# ---------------------------------------------------------------------------
# Per-instrument tuning. Spleeter's 4stems model always outputs all four
# stems (vocals, drums, bass, other) in one pass, so "instrument selection"
# is just: (a) which stem to keep, and (b) what transcription parameters
# suit it. Bass and (mono-melodic) vocals are close to monophonic, so we
# enforce single-note-at-a-time. Drums are not pitched the same way (needs
# an onset/percussion-specific transcription approach, not basic-pitch's
# pitch model) and "other" is often genuinely polyphonic (guitar/keys
# chords) -- both need different handling than what's below, which is why
# they're marked as not-yet-implemented rather than silently mistranscribed.
# ---------------------------------------------------------------------------
@dataclass
class InstrumentProfile:
    stem: str  # which Spleeter 4stems output to use
    onset_threshold: float
    frame_threshold: float
    minimum_note_length_ms: float
    minimum_frequency_hz: Optional[float]
    maximum_frequency_hz: Optional[float]
    enforce_monophonic: bool
    midi_program: int  # General MIDI program number (0-indexed)
    instrument_name: str
    clef: str = "bass"
    string_tuning: str = "bass-four-string-tuning"  # LilyPond predefined tuning name
    with_tab: bool = True


INSTRUMENT_PROFILES = {
    "bass": InstrumentProfile(
        stem="bass",
        onset_threshold=0.6,
        frame_threshold=0.4,
        minimum_note_length_ms=100,
        minimum_frequency_hz=30,   # ~B0, covers 4/5-string bass low range
        maximum_frequency_hz=350,  # ~F4, generous upper range incl. slap/harmonics
        enforce_monophonic=True,
        midi_program=33,  # Electric Bass (finger)
        instrument_name="Electric Bass",
        clef="bass",
        string_tuning="bass-four-string-tuning",
        with_tab=True,
    ),
    # "vocals": ...   TODO: melody-line extraction, still monophonic-ish
    # "drums":  TODO: needs an onset/percussion transcriber, not basic-pitch
    # "other":  TODO: often genuinely polyphonic (guitar/keys) -- needs
    #                 multi-voice notation handling, not a monophonic pass
}


# ---------------------------------------------------------------------------
# Step 1: stem separation, chunked to keep memory bounded on small hosts.
# ---------------------------------------------------------------------------
def get_duration(path: str) -> float:
    out = subprocess.run(
        [bin_path("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _extract_chunk(path: str, start: float, dur: float, out_path: str) -> None:
    subprocess.run(
        [bin_path("ffmpeg"), "-y", "-ss", str(start), "-t", str(dur), "-i", path,
         "-ac", "2", "-ar", str(SR), out_path, "-loglevel", "error"],
        check=True,
    )


def _crossfade_concat(chunks: list[np.ndarray], overlap_s: float, sr: int) -> np.ndarray:
    if not chunks:
        return np.zeros((0, 2))
    overlap_n = int(overlap_s * sr)
    result = chunks[0]
    for nxt in chunks[1:]:
        if overlap_n > 0 and len(result) >= overlap_n and len(nxt) >= overlap_n:
            fade_out = np.linspace(1, 0, overlap_n)[:, None]
            fade_in = np.linspace(0, 1, overlap_n)[:, None]
            tail = result[-overlap_n:] * fade_out + nxt[:overlap_n] * fade_in
            result = np.concatenate([result[:-overlap_n], tail, nxt[overlap_n:]], axis=0)
        else:
            result = np.concatenate([result, nxt], axis=0)
    return result


def separate_stem(
    input_path: str,
    stem: str,
    work_dir: str,
    chunk_seconds: float = 60,
    overlap_seconds: float = 2,
    models_root: Optional[str] = None,
) -> str:
    """Isolate one Spleeter 4stems stem from the whole track, processing it
    in chunks to keep peak memory bounded (tested to fit in ~2GB RAM per
    60s chunk; the alternative -- feeding the whole track at once -- scales
    memory with track duration and can OOM on modest hosts)."""
    if models_root is None:
        # A per-user persistent location, not a relative path -- a packaged
        # .app's working directory isn't guaranteed writable or predictable.
        models_root = os.path.join(os.path.expanduser("~"), ".basse2partition", "models")
    ensure_model("4stems", models_root=models_root)
    # Point Spleeter's own ModelProvider at the dir ensure_model() just
    # populated. Without this it looks under ./pretrained_models, doesn't
    # find the model, and tries to re-download it through httpx[http2]
    # (which we don't ship) instead of using the copy we already have.
    # ModelProvider.DEFAULT_MODEL_PATH is read from $MODEL_PATH once, at
    # import time (before this function runs), so setting the env var here
    # is not enough -- patch the class attribute directly as well.
    os.environ["MODEL_PATH"] = models_root
    from spleeter.model.provider import ModelProvider
    ModelProvider.DEFAULT_MODEL_PATH = models_root
    duration = get_duration(input_path)
    separator = Separator("spleeter:4stems")

    chunks_dir = os.path.join(work_dir, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)

    step = chunk_seconds - overlap_seconds
    starts = list(np.arange(0, duration, step))
    stem_arrays = []
    for i, start in enumerate(starts):
        dur = min(chunk_seconds, duration - start)
        if dur <= 0:
            continue
        chunk_in = os.path.join(chunks_dir, f"in_{i:03d}.wav")
        chunk_out_dir = os.path.join(chunks_dir, f"out_{i:03d}")
        _extract_chunk(input_path, start, dur, chunk_in)
        separator.separate_to_file(
            chunk_in, chunk_out_dir, filename_format="{instrument}.{codec}"
        )
        stem_path = os.path.join(chunk_out_dir, f"{stem}.Codec.WAV")
        audio, sr = sf.read(stem_path, always_2d=True)
        assert sr == SR
        stem_arrays.append(audio)
        os.remove(chunk_in)
        shutil.rmtree(chunk_out_dir, ignore_errors=True)

    full_audio = _crossfade_concat(stem_arrays, overlap_seconds, SR)
    out_path = os.path.join(work_dir, f"{stem}.wav")
    sf.write(out_path, full_audio, SR)
    return out_path


# ---------------------------------------------------------------------------
# Step 2: audio -> MIDI transcription.
# ---------------------------------------------------------------------------
def transcribe_to_midi(audio_path: str, profile: InstrumentProfile, out_path: str) -> None:
    from basic_pitch.inference import predict
    from basic_pitch import ICASSP_2022_MODEL_PATH

    # ICASSP_2022_MODEL_PATH normally points at basic-pitch's TensorFlow
    # SavedModel, which failed to restore under our TF version -- use the
    # ONNX variant shipped alongside it in the same package instead.
    # Resolved dynamically (not a hardcoded path) so this also works from a
    # PyInstaller bundle, where site-packages lives somewhere else entirely.
    onnx_model = os.path.join(os.path.dirname(str(ICASSP_2022_MODEL_PATH)), "nmp.onnx")

    _, midi_data, _ = predict(
        audio_path,
        model_or_model_path=onnx_model,
        onset_threshold=profile.onset_threshold,
        frame_threshold=profile.frame_threshold,
        minimum_note_length=profile.minimum_note_length_ms,
        minimum_frequency=profile.minimum_frequency_hz,
        maximum_frequency=profile.maximum_frequency_hz,
    )
    midi_data.write(out_path)


def enforce_monophonic(midi_path: str, tempo_bpm: float, out_path: str) -> int:
    """Rewrite a MIDI file so at most one note sounds at a time, keeping the
    louder note on overlaps and trimming the quieter/earlier one -- suited
    to naturally monophonic lines like bass or a lead melody."""
    orig = pm.PrettyMIDI(midi_path)
    all_notes = [n for i in orig.instruments for n in i.notes]
    all_notes.sort(key=lambda n: n.start)

    mono: list[pm.Note] = []
    for n in all_notes:
        if mono and n.start < mono[-1].end:
            if n.start - mono[-1].start > 0.03:
                mono[-1].end = n.start
            else:
                if n.velocity > mono[-1].velocity:
                    mono[-1] = n
                continue
        mono.append(n)
    mono = [n for n in mono if (n.end - n.start) > 0.03]

    new = pm.PrettyMIDI(initial_tempo=tempo_bpm)
    inst = pm.Instrument(program=33, name="Electric Bass")
    inst.notes = mono
    new.instruments.append(inst)
    new.write(out_path)
    return len(mono)


def estimate_tempo_bpm(audio_path: str) -> float:
    import librosa

    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    if not np.isfinite(bpm) or bpm <= 0:
        # Beat tracking returns 0 on material with no clear transients
        # (sustained/ambient passages, very short clips). Fall back to a
        # neutral tempo instead of dividing by zero downstream.
        bpm = 120.0
    return bpm


# ---------------------------------------------------------------------------
# Step 3: MIDI -> notated score (MusicXML + PDF via LilyPond).
# ---------------------------------------------------------------------------
def midi_to_score(
    midi_path: str,
    profile: InstrumentProfile,
    tempo_bpm: float,
    title: str,
    musicxml_out: str,
    pdf_out: str,
) -> None:
    """Write both a MusicXML (for import into other notation software) and
    a PDF with standard notation + tab (for bass/guitar-family instruments).

    The MusicXML comes from music21 (solid, general-purpose). The PDF comes
    from a dedicated direct MIDI->LilyPond exporter (lilypond_export.py)
    instead of music21's own LilyPond writer: music21's writer emits nested
    "\\new Voice" constructs for certain rhythms, and instantiating that same
    music under two different staves (standard + tab) hits a LilyPond
    context-reuse limitation that corrupts the tab rendering. The direct
    exporter sidesteps this by building plain, voice-free LilyPond output.
    """
    import music21 as m21

    from lilypond_export import render_score

    score = m21.converter.parse(
        midi_path, quantizePost=True, quarterLengthDivisors=(4, 3)
    )
    for part in score.parts:
        part.insert(0, m21.instrument.fromString(profile.instrument_name))
        part.insert(0, m21.meter.TimeSignature("4/4"))
    score.insert(0, m21.tempo.MetronomeMark(number=round(tempo_bpm)))
    score.metadata = m21.metadata.Metadata()
    score.metadata.title = title
    score.metadata.composer = "transcription automatique"
    score.write("musicxml", fp=musicxml_out)

    render_score(
        midi_path,
        tempo_bpm=tempo_bpm,
        title=title,
        pdf_out=pdf_out,
        clef=profile.clef,
        string_tuning=profile.string_tuning,
        with_tab=profile.with_tab,
    )


# ---------------------------------------------------------------------------
# End-to-end orchestration.
# ---------------------------------------------------------------------------
def run_pipeline(
    input_path: str,
    instrument: str,
    output_dir: str,
    chunk_seconds: float = 60,
    overlap_seconds: float = 2,
    tempo_bpm: Optional[float] = None,
    title: Optional[str] = None,
) -> dict:
    if instrument not in INSTRUMENT_PROFILES:
        raise ValueError(
            f"Instrument {instrument!r} not supported yet. "
            f"Available: {list(INSTRUMENT_PROFILES)}"
        )
    profile = INSTRUMENT_PROFILES[instrument]
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    title = title or f"{base_name} ({profile.instrument_name} - transcription automatique)"

    with tempfile.TemporaryDirectory() as work_dir:
        stem_wav = separate_stem(
            input_path, profile.stem, work_dir,
            chunk_seconds=chunk_seconds, overlap_seconds=overlap_seconds,
        )

        if tempo_bpm is None:
            tempo_bpm = estimate_tempo_bpm(stem_wav)

        raw_midi = os.path.join(work_dir, "raw.mid")
        transcribe_to_midi(stem_wav, profile, raw_midi)

        final_midi = os.path.join(output_dir, f"{base_name}_{instrument}.mid")
        if profile.enforce_monophonic:
            enforce_monophonic(raw_midi, tempo_bpm, final_midi)
        else:
            shutil.copy(raw_midi, final_midi)

        musicxml_out = os.path.join(output_dir, f"{base_name}_{instrument}.musicxml")
        pdf_out = os.path.join(output_dir, f"{base_name}_{instrument}.pdf")
        midi_to_score(final_midi, profile, tempo_bpm, title, musicxml_out, pdf_out)

        stem_audio_out = os.path.join(output_dir, f"{base_name}_{instrument}.wav")
        shutil.copy(stem_wav, stem_audio_out)

    return {
        "pdf": pdf_out,
        "musicxml": musicxml_out,
        "midi": final_midi,
        "isolated_audio": stem_audio_out,
        "tempo_bpm": tempo_bpm,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Path to the input audio file (mp3, wav, ...)")
    parser.add_argument("--instrument", default="bass", choices=list(INSTRUMENT_PROFILES))
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--chunk-seconds", type=float, default=60)
    parser.add_argument("--overlap-seconds", type=float, default=2)
    parser.add_argument("--tempo-bpm", type=float, default=None,
                         help="Force a tempo instead of auto-detecting it")
    args = parser.parse_args()

    result = run_pipeline(
        args.input, args.instrument, args.output_dir,
        chunk_seconds=args.chunk_seconds, overlap_seconds=args.overlap_seconds,
        tempo_bpm=args.tempo_bpm,
    )
    print(f"Tempo used: {result['tempo_bpm']:.1f} BPM")
    print(f"PDF:      {result['pdf']}")
    print(f"MusicXML: {result['musicxml']}")
    print(f"MIDI:     {result['midi']}")
    print(f"Isolated audio: {result['isolated_audio']}")


if __name__ == "__main__":
    main()
