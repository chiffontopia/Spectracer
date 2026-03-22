from __future__ import annotations

from pathlib import Path

import pytest

from spectracer.midi import synth as synth_module
from spectracer.midi.editor_model import MidiChannelConfig
from spectracer.midi.synth import NullSynthBackend, create_default_midi_synth, resolve_soundfont_path


class _FakeMessage:
    def __init__(self, message_type: str, **kwargs) -> None:
        self.type = message_type
        self.kwargs = kwargs


class _FakePort:
    def __init__(self) -> None:
        self.sent: list[_FakeMessage] = []
        self.closed = False

    def send(self, message: _FakeMessage) -> None:
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True


class _FakeMidoModule:
    Message = _FakeMessage

    def __init__(self, output_names: list[str]) -> None:
        self._output_names = list(output_names)
        self.port = _FakePort()
        self.opened_name: str | None = None
        self.autoreset: bool | None = None

    def get_output_names(self) -> list[str]:
        return list(self._output_names)

    def open_output(self, name: str, autoreset: bool = False) -> _FakePort:
        self.opened_name = name
        self.autoreset = autoreset
        return self.port


class _FakeFluidSynthInstance:
    def __init__(self, gain: float, samplerate: int) -> None:
        self.gain = gain
        self.samplerate = samplerate
        self.started_drivers: list[str | None] = []
        self.loaded_soundfonts: list[str] = []
        self.program_select_calls: list[tuple[int, int, int, int]] = []
        self.note_on_calls: list[tuple[int, int, int]] = []
        self.note_off_calls: list[tuple[int, int]] = []
        self.cc_calls: list[tuple[int, int, int]] = []
        self.deleted = False

    def start(self, driver: str | None = None) -> None:
        self.started_drivers.append(driver)

    def sfload(self, path: str, reset_presets: bool | None = None) -> int:
        _ = reset_presets
        self.loaded_soundfonts.append(path)
        return 1

    def program_select(self, channel: int, sfid: int, bank: int, program: int) -> int:
        self.program_select_calls.append((channel, sfid, bank, program))
        return 0

    def noteon(self, channel: int, note: int, velocity: int) -> None:
        self.note_on_calls.append((channel, note, velocity))

    def noteoff(self, channel: int, note: int) -> None:
        self.note_off_calls.append((channel, note))

    def cc(self, channel: int, control: int, value: int) -> None:
        self.cc_calls.append((channel, control, value))

    def all_notes_off(self, channel: int) -> None:
        _ = channel

    def all_sounds_off(self, channel: int) -> None:
        _ = channel

    def system_reset(self) -> None:
        return

    def delete(self) -> None:
        self.deleted = True


class _FakeFluidSynthModule:
    def __init__(self) -> None:
        self.instances: list[_FakeFluidSynthInstance] = []

    def Synth(self, gain: float, samplerate: int) -> _FakeFluidSynthInstance:  # noqa: N802
        instance = _FakeFluidSynthInstance(gain=gain, samplerate=samplerate)
        self.instances.append(instance)
        return instance


def test_resolve_soundfont_path_accepts_explicit_file(tmp_path: Path) -> None:
    soundfont = tmp_path / "demo.sf2"
    soundfont.write_bytes(b"sf2")

    assert resolve_soundfont_path(soundfont) == soundfont.resolve()


def test_resolve_soundfont_path_uses_environment_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    soundfont = tmp_path / "env.sf2"
    soundfont.write_bytes(b"sf2")
    monkeypatch.setenv("SPECTRACER_SOUNDFONT", str(soundfont))

    assert resolve_soundfont_path() == soundfont.resolve()


def test_resolve_soundfont_path_scans_soundfonts_directory_for_any_sf2_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soundfonts_dir = tmp_path / "soundfonts"
    soundfonts_dir.mkdir()

    target = tmp_path / "targets" / "SGM-V2.01.sf2"
    target.parent.mkdir()
    target.write_bytes(b"sf2")

    candidate = soundfonts_dir / "SGM-V2.01.sf2"
    try:
        candidate.symlink_to(target)
    except OSError:
        candidate.write_bytes(target.read_bytes())

    monkeypatch.delenv("SPECTRACER_SOUNDFONT", raising=False)
    monkeypatch.setattr(synth_module, "iter_soundfont_search_dirs", lambda: [soundfonts_dir])

    assert resolve_soundfont_path() == candidate.resolve()


def test_null_synth_backend_is_safe_noop() -> None:
    backend = NullSynthBackend("missing soundfont")

    backend.note_on(60, velocity=100)
    backend.note_off(60)
    backend.set_master_gain(0.5)
    backend.set_channel_volume(0, 90)
    backend.set_channel_pan(0, 32)
    backend.set_channel_program(0, bank=0, program=1)
    backend.panic()
    backend.close()

    assert backend.status.available is False
    assert backend.status.backend_name == "null"


def test_create_default_midi_synth_falls_back_to_system_midi_output_when_no_soundfont(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mido = _FakeMidoModule(["Microsoft GS Wavetable Synth 0"])

    def _import_module(name: str):
        if name == "mido":
            return fake_mido
        if name == "fluidsynth":
            raise ImportError("no fluidsynth")
        raise ImportError(name)

    monkeypatch.delenv("SPECTRACER_SOUNDFONT", raising=False)
    monkeypatch.setattr(synth_module, "iter_soundfont_search_dirs", lambda: [])
    monkeypatch.setattr(synth_module.importlib, "import_module", _import_module)

    synth = create_default_midi_synth(channel=2, program=40)
    synth.note_on(60, velocity=100)
    synth.note_off(60)
    synth.panic()
    synth.close()

    assert synth.is_available is True
    assert synth.status.backend_name == "midi_output"
    assert synth.status.output_name == "Microsoft GS Wavetable Synth 0"
    assert fake_mido.opened_name == "Microsoft GS Wavetable Synth 0"
    assert any(
        message.type == "program_change" and message.kwargs.get("channel") == 2 and message.kwargs.get("program") == 40
        for message in fake_mido.port.sent
    )
    assert any(
        message.type == "note_on" and message.kwargs.get("channel") == 2 and message.kwargs.get("note") == 60
        for message in fake_mido.port.sent
    )
    assert fake_mido.port.closed is True


def test_create_default_midi_synth_prefers_explicit_output_port(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mido = _FakeMidoModule(["LoopMIDI Port", "Microsoft GS Wavetable Synth 0"])

    def _import_module(name: str):
        if name == "mido":
            return fake_mido
        if name == "fluidsynth":
            raise AssertionError("explicit MIDI output should bypass fluidsynth auto path")
        raise ImportError(name)

    monkeypatch.setattr(synth_module.importlib, "import_module", _import_module)

    synth = create_default_midi_synth(output_name="LoopMIDI", channel=4, program=10)
    synth.note_on(64, velocity=90)

    assert synth.is_available is True
    assert synth.status.backend_name == "midi_output"
    assert synth.status.output_name == "LoopMIDI Port"
    assert fake_mido.opened_name == "LoopMIDI Port"
    assert any(
        message.type == "note_on" and message.kwargs.get("channel") == 4 and message.kwargs.get("note") == 64
        for message in fake_mido.port.sent
    )


def test_create_default_midi_synth_prefers_system_output_before_auto_soundfont(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soundfonts_dir = tmp_path / "soundfonts"
    soundfonts_dir.mkdir()
    (soundfonts_dir / "auto.sf2").write_bytes(b"sf2")

    fake_mido = _FakeMidoModule(["Microsoft GS Wavetable Synth 0"])
    fake_fluidsynth = _FakeFluidSynthModule()

    def _import_module(name: str):
        if name == "mido":
            return fake_mido
        if name == "fluidsynth":
            return fake_fluidsynth
        raise ImportError(name)

    monkeypatch.delenv("SPECTRACER_SOUNDFONT", raising=False)
    monkeypatch.setattr(synth_module, "iter_soundfont_search_dirs", lambda: [soundfonts_dir])
    monkeypatch.setattr(synth_module.importlib, "import_module", _import_module)

    synth = create_default_midi_synth(channel=1, program=5)

    assert synth.is_available is True
    assert synth.status.backend_name == "midi_output"
    assert fake_mido.opened_name == "Microsoft GS Wavetable Synth 0"
    assert fake_fluidsynth.instances == []


def test_create_default_midi_synth_prefers_explicit_soundfont_before_auto_system_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soundfont = tmp_path / "preferred.sf2"
    soundfont.write_bytes(b"sf2")

    fake_mido = _FakeMidoModule(["Microsoft GS Wavetable Synth 0"])
    fake_fluidsynth = _FakeFluidSynthModule()

    def _import_module(name: str):
        if name == "mido":
            return fake_mido
        if name == "fluidsynth":
            return fake_fluidsynth
        raise ImportError(name)

    monkeypatch.setattr(synth_module.importlib, "import_module", _import_module)

    synth = create_default_midi_synth(soundfont_path=soundfont, channel=3, program=12)

    assert synth.is_available is True
    assert synth.status.backend_name == "fluidsynth"
    assert fake_mido.opened_name is None
    assert len(fake_fluidsynth.instances) == 1
    instance = fake_fluidsynth.instances[0]
    assert instance.loaded_soundfonts == [str(soundfont.resolve())]
    assert instance.program_select_calls == [(3, 1, 0, 12)]


def test_create_default_midi_synth_uses_system_midi_output_when_fluidsynth_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soundfont = tmp_path / "fallback.sf2"
    soundfont.write_bytes(b"sf2")
    fake_mido = _FakeMidoModule(["Microsoft GS Wavetable Synth 0"])

    def _import_module(name: str):
        if name == "mido":
            return fake_mido
        if name == "fluidsynth":
            raise ImportError("no fluidsynth")
        raise ImportError(name)

    monkeypatch.setattr(synth_module.importlib, "import_module", _import_module)

    synth = create_default_midi_synth(soundfont_path=soundfont)

    assert synth.is_available is True
    assert synth.status.backend_name == "midi_output"
    assert "fluidsynth" in synth.status.message.lower()


def test_create_default_midi_synth_uses_drum_bank_on_channel_ten_with_system_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mido = _FakeMidoModule(["LoopMIDI Port"])

    def _import_module(name: str):
        if name == "mido":
            return fake_mido
        raise ImportError(name)

    monkeypatch.setattr(synth_module.importlib, "import_module", _import_module)

    synth = create_default_midi_synth(output_name="LoopMIDI", channel=9, program=16)

    assert synth.is_available is True
    assert synth.status.backend_name == "midi_output"
    assert any(
        message.type == "control_change"
        and message.kwargs.get("channel") == 9
        and message.kwargs.get("control") == 0
        and message.kwargs.get("value") == 1
        for message in fake_mido.port.sent
    )
    assert any(
        message.type == "control_change"
        and message.kwargs.get("channel") == 9
        and message.kwargs.get("control") == 32
        and message.kwargs.get("value") == 0
        for message in fake_mido.port.sent
    )
    assert any(
        message.type == "program_change"
        and message.kwargs.get("channel") == 9
        and message.kwargs.get("program") == 16
        for message in fake_mido.port.sent
    )


def test_create_default_midi_synth_uses_drum_bank_on_channel_ten_with_fluidsynth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soundfont = tmp_path / "drums.sf2"
    soundfont.write_bytes(b"sf2")
    fake_fluidsynth = _FakeFluidSynthModule()

    def _import_module(name: str):
        if name == "fluidsynth":
            return fake_fluidsynth
        raise ImportError(name)

    monkeypatch.setattr(synth_module.importlib, "import_module", _import_module)

    synth = create_default_midi_synth(soundfont_path=soundfont, channel=9, program=40)

    assert synth.is_available is True
    assert synth.status.backend_name == "fluidsynth"
    assert len(fake_fluidsynth.instances) == 1
    instance = fake_fluidsynth.instances[0]
    assert instance.program_select_calls == [(9, 1, 128, 40)]


def test_midi_synth_supports_runtime_channel_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mido = _FakeMidoModule(["LoopMIDI Port"])

    def _import_module(name: str):
        if name == "mido":
            return fake_mido
        raise ImportError(name)

    monkeypatch.setattr(synth_module.importlib, "import_module", _import_module)

    synth = create_default_midi_synth(output_name="LoopMIDI", channel=1, program=8)
    synth.set_master_gain(0.5)
    synth.set_channel_volume(2, 80)
    synth.set_channel_pan(2, 32)
    synth.set_channel_program(2, bank=0, program=33)
    synth.apply_channel_config(MidiChannelConfig(channel=2, program=34, pan=48, muted=True))

    assert any(
        message.type == "control_change"
        and message.kwargs.get("channel") == 2
        and message.kwargs.get("control") == 7
        and message.kwargs.get("value") == 40
        for message in fake_mido.port.sent
    )
    assert any(
        message.type == "control_change"
        and message.kwargs.get("channel") == 2
        and message.kwargs.get("control") == 10
        and message.kwargs.get("value") == 48
        for message in fake_mido.port.sent
    )
    assert any(
        message.type == "program_change"
        and message.kwargs.get("channel") == 2
        and message.kwargs.get("program") == 34
        for message in fake_mido.port.sent
    )
