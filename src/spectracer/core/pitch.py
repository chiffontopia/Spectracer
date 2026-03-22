from __future__ import annotations

import math

NOTE_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
BLACK_KEY_CLASSES = {1, 3, 6, 8, 10}


def frequency_to_midi(frequency_hz: float, *, a4_hz: float = 440.0) -> float:
    if frequency_hz <= 0:
        raise ValueError("frequency_hz 必须大于 0")
    return 69.0 + 12.0 * math.log2(frequency_hz / a4_hz)


def midi_to_note_name(midi_note: float) -> str:
    midi_rounded = int(round(midi_note))
    note_class = midi_rounded % 12
    octave = (midi_rounded // 12) - 1
    return f"{NOTE_NAMES_SHARP[note_class]}{octave}"


def frequency_to_note_name(frequency_hz: float, *, a4_hz: float = 440.0) -> str:
    return midi_to_note_name(frequency_to_midi(frequency_hz, a4_hz=a4_hz))


def is_black_key(midi_note: float) -> bool:
    return int(round(midi_note)) % 12 in BLACK_KEY_CLASSES
