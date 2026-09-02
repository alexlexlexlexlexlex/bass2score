"""Direct MIDI -> LilyPond (standard notation + bass tab) exporter.
Self-contained: does not depend on music21's LilyPond writer, which has a
context-reuse bug when the same music is instantiated under two different
staves (Staff + TabStaff) for content containing nested Voice constructs.
"""
import pretty_midi as pm

PITCH_CLASSES = ['c','cis','d','dis','e','f','fis','g','gis','a','ais','b']

# (length_in_16ths, lilypond_duration_token)
DURATIONS = [
    (16, '1'), (12, '2.'), (8, '2'), (6, '4.'), (4, '4'),
    (3, '8.'), (2, '8'), (1, '16'),
]


def midi_to_lily_pitch(midi_note: int) -> str:
    diff = midi_note - 48  # unmarked 'c' in LilyPond absolute pitch = MIDI 48
    octave = diff // 12
    pitch_index = diff % 12
    name = PITCH_CLASSES[pitch_index]
    if octave > 0:
        name += "'" * octave
    elif octave < 0:
        name += "," * (-octave)
    return name


def decompose_duration(n_16ths: int):
    tokens = []
    remaining = n_16ths
    while remaining > 0:
        for length, token in DURATIONS:
            if length <= remaining:
                tokens.append(token)
                remaining -= length
                break
        else:
            break  # shouldn't happen (1 always fits)
    return tokens


def build_lilypond_body(notes, tempo_bpm: float, time_sig_beats: int = 4) -> str:
    """notes: list of pretty_midi.Note (monophonic, non-overlapping).
    Returns a LilyPond music expression (no \\new Staff wrapper)."""
    sixteenth_sec = (60.0 / tempo_bpm) / 4.0
    sixteenths_per_measure = time_sig_beats * 4

    # Build a run-length-encoded timeline: list of (pitch_or_None, length_in_16ths)
    timeline = []
    cursor_16 = 0
    for n in sorted(notes, key=lambda x: x.start):
        start_16 = round(n.start / sixteenth_sec)
        end_16 = round(n.end / sixteenth_sec)
        end_16 = max(end_16, start_16 + 1)
        if start_16 > cursor_16:
            timeline.append((None, start_16 - cursor_16))
        timeline.append((n.pitch, end_16 - start_16))
        cursor_16 = end_16

    lines = []
    measure_pos = 0
    line_buf = []
    for pitch, length in timeline:
        remaining = length
        first = True
        while remaining > 0:
            space_in_measure = sixteenths_per_measure - measure_pos
            take = min(remaining, space_in_measure) if space_in_measure > 0 else remaining
            take = max(take, 1)
            for tok_len, tok in DURATIONS:
                if tok_len <= take:
                    chunk = tok_len
                    tok_used = tok
                    break
            else:
                chunk, tok_used = 1, '16'
            if pitch is None:
                line_buf.append(f"r{tok_used}")
            else:
                pname = midi_to_lily_pitch(pitch)
                suffix = "~" if (chunk < remaining) else ""
                line_buf.append(f"{pname}{tok_used}{suffix}")
            remaining -= chunk
            measure_pos += chunk
            if measure_pos >= sixteenths_per_measure:
                line_buf.append("|")
                lines.append(" ".join(line_buf))
                line_buf = []
                measure_pos = 0
            first = False
    if line_buf:
        lines.append(" ".join(line_buf))

    return "\n".join(lines)


def render_score(
    midi_path: str,
    tempo_bpm: float,
    title: str,
    pdf_out: str,
    clef: str = "bass",
    string_tuning: str = "bass-four-string-tuning",
    with_tab: bool = True,
):
    midi = pm.PrettyMIDI(midi_path)
    notes = [n for inst in midi.instruments for n in inst.notes]
    body = build_lilypond_body(notes, tempo_bpm)

    tab_block = ""
    if with_tab:
        tab_block = f'''
    \\new TabStaff \\with {{ stringTunings = #{string_tuning} }} {{
      \\clef "{clef}"
      \\time 4/4
      {body}
    }}'''

    ly = f'''\\version "2.24.3"
\\header {{ title = "{title}" tagline = ##f }}

\\score {{
  <<
    \\new Staff {{
      \\clef "{clef}"
      \\time 4/4
      {body}
    }}{tab_block}
  >>
  \\layout {{ }}
  \\midi {{ \\tempo 4 = {round(tempo_bpm)} }}
}}
'''
    ly_path = pdf_out[:-4] + ".ly" if pdf_out.endswith(".pdf") else pdf_out + ".ly"
    # LilyPond assumes UTF-8 source; also, on Windows the default open()
    # encoding is the locale codepage (cp1252), which raises
    # UnicodeEncodeError on accented characters common in track titles.
    with open(ly_path, "w", encoding="utf-8") as f:
        f.write(ly)

    import subprocess
    from bundled_bins import bin_path
    pdf_base = pdf_out[:-4] if pdf_out.endswith(".pdf") else pdf_out
    proc = subprocess.run([bin_path("lilypond"), "-o", pdf_base, ly_path],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "LilyPond n'a pas pu produire le PDF "
            f"(code {proc.returncode}).\n{proc.stderr or proc.stdout}".strip()
        )
    return pdf_out
