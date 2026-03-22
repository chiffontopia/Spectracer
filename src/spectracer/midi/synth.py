from __future__ import annotations

import importlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from spectracer.midi.editor_model import MidiChannelConfig
from spectracer.midi.gm import effective_midi_bank

DEFAULT_MIDI_CHANNEL = 0
DEFAULT_MIDI_PROGRAM = 0
DEFAULT_MIDI_BANK = 0
DEFAULT_SAMPLE_RATE = 44_100
DEFAULT_GAIN = 0.6
DEFAULT_CHANNEL_VOLUME = 100
DEFAULT_CHANNEL_PAN = 64

_SOUNDFONT_PRIORITY_NAMES = {
    "default.sf2": 0,
    "fluidr3_gm.sf2": 1,
}
_GS_WAVETABLE_KEYWORDS = (
    "microsoft gs wavetable synth",
    "gs wavetable",
    "microsoft midi mapper",
    "midi mapper",
)


@dataclass(slots=True, frozen=True)
class SynthBackendStatus:
    backend_name: str
    available: bool
    message: str
    soundfont_path: Path | None = None
    output_name: str | None = None


class SynthBackend(ABC):
    @property
    @abstractmethod
    def status(self) -> SynthBackendStatus:
        raise NotImplementedError

    @abstractmethod
    def note_on(self, note: int, velocity: int = 100, *, channel: int | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def note_off(self, note: int, *, channel: int | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_master_gain(self, value: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_channel_volume(self, channel: int, value: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_channel_pan(self, channel: int, value: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_channel_program(self, channel: int, *, bank: int, program: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def panic(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class NullSynthBackend(SynthBackend):
    def __init__(self, message: str, *, soundfont_path: Path | None = None, output_name: str | None = None) -> None:
        self._status = SynthBackendStatus(
            backend_name="null",
            available=False,
            message=str(message).strip() or "MIDI synth unavailable",
            soundfont_path=soundfont_path,
            output_name=output_name,
        )

    @property
    def status(self) -> SynthBackendStatus:
        return self._status

    def note_on(self, note: int, velocity: int = 100, *, channel: int | None = None) -> None:
        _ = note
        _ = velocity
        _ = channel

    def note_off(self, note: int, *, channel: int | None = None) -> None:
        _ = note
        _ = channel

    def set_master_gain(self, value: float) -> None:
        _ = value

    def set_channel_volume(self, channel: int, value: int) -> None:
        _ = channel
        _ = value

    def set_channel_pan(self, channel: int, value: int) -> None:
        _ = channel
        _ = value

    def set_channel_program(self, channel: int, *, bank: int, program: int) -> None:
        _ = channel
        _ = bank
        _ = program

    def panic(self) -> None:
        return

    def close(self) -> None:
        return


class FluidSynthBackend(SynthBackend):
    def __init__(
        self,
        soundfont_path: str | Path,
        *,
        channel: int = DEFAULT_MIDI_CHANNEL,
        bank: int = DEFAULT_MIDI_BANK,
        program: int = DEFAULT_MIDI_PROGRAM,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        gain: float = DEFAULT_GAIN,
        fallback_reason: str | None = None,
    ) -> None:
        resolved_soundfont = resolve_soundfont_path(soundfont_path)
        if resolved_soundfont is None:
            raise FileNotFoundError("未找到可用 SoundFont")

        try:
            fluidsynth = importlib.import_module("fluidsynth")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("未安装 pyfluidsynth / fluidsynth 模块") from exc

        self._soundfont_path = resolved_soundfont
        self._channel = _normalize_channel(channel)
        self._master_gain = 1.0
        self._channel_volumes = {midi_channel: DEFAULT_CHANNEL_VOLUME for midi_channel in range(16)}
        self._synth = fluidsynth.Synth(gain=1.0, samplerate=int(sample_rate))
        status_message = f"使用 FluidSynth：{resolved_soundfont.name}"
        if fallback_reason:
            status_message = f"{status_message}（{fallback_reason}）"
        self._status = SynthBackendStatus(
            backend_name="fluidsynth",
            available=True,
            message=status_message,
            soundfont_path=resolved_soundfont,
            output_name=None,
        )

        try:
            self._start_audio_driver()
            self._sfid = self._load_soundfont(resolved_soundfont)
            self._select_program(bank=bank, program=program)
            self.set_master_gain(gain)
        except Exception:
            self.close()
            raise

    @property
    def status(self) -> SynthBackendStatus:
        return self._status

    def note_on(self, note: int, velocity: int = 100, *, channel: int | None = None) -> None:
        self._synth.noteon(
            self._resolve_channel(channel),
            _clamp_midi_data(note),
            _clamp_midi_data(velocity),
        )

    def note_off(self, note: int, *, channel: int | None = None) -> None:
        self._synth.noteoff(self._resolve_channel(channel), _clamp_midi_data(note))

    def set_master_gain(self, value: float) -> None:
        self._master_gain = _clamp_gain(value)
        for midi_channel, raw_volume in self._channel_volumes.items():
            self._send_control_change(midi_channel, 7, self._effective_channel_volume(raw_volume))

    def set_channel_volume(self, channel: int, value: int) -> None:
        normalized_channel = self._resolve_channel(channel)
        raw_volume = _clamp_midi_data(value)
        self._channel_volumes[normalized_channel] = raw_volume
        self._send_control_change(normalized_channel, 7, self._effective_channel_volume(raw_volume))

    def set_channel_pan(self, channel: int, value: int) -> None:
        self._send_control_change(self._resolve_channel(channel), 10, _clamp_midi_data(value))

    def set_channel_program(self, channel: int, *, bank: int, program: int) -> None:
        normalized_channel = self._resolve_channel(channel)
        effective_bank = effective_midi_bank(normalized_channel, bank)
        result = self._synth.program_select(
            normalized_channel,
            int(self._sfid),
            effective_bank,
            _clamp_midi_data(program),
        )
        if isinstance(result, int) and result < 0:
            raise RuntimeError("FluidSynth program_select 失败")

    def panic(self) -> None:
        for midi_channel in range(16):
            try:
                self._synth.all_notes_off(midi_channel)
            except Exception:  # noqa: BLE001
                pass
            try:
                self._synth.all_sounds_off(midi_channel)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._synth.system_reset()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        synth = getattr(self, "_synth", None)
        if synth is None:
            return
        try:
            self.panic()
        finally:
            try:
                synth.delete()
            except Exception:  # noqa: BLE001
                pass
            self._synth = None

    def _start_audio_driver(self) -> None:
        errors: list[str] = []
        driver_candidates: list[str | None] = []
        if os.name == "nt":
            driver_candidates.extend(["dsound", "wasapi", "winmm"])
        driver_candidates.append(None)

        for driver in driver_candidates:
            try:
                if driver is None:
                    self._synth.start()
                else:
                    self._synth.start(driver=driver)
                return
            except TypeError:
                try:
                    self._synth.start()
                    return
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
            except Exception as exc:  # noqa: BLE001
                label = "default" if driver is None else driver
                errors.append(f"{label}: {exc}")

        joined = "; ".join(errors) if errors else "unknown error"
        raise RuntimeError(f"FluidSynth 音频驱动启动失败: {joined}")

    def _load_soundfont(self, soundfont_path: Path) -> int:
        try:
            sfid = self._synth.sfload(str(soundfont_path), True)
        except TypeError:
            sfid = self._synth.sfload(str(soundfont_path))
        if int(sfid) < 0:
            raise RuntimeError(f"SoundFont 加载失败: {soundfont_path}")
        return int(sfid)

    def _select_program(self, *, bank: int, program: int) -> None:
        self.set_channel_program(self._channel, bank=bank, program=program)

    def _send_control_change(self, channel: int, control: int, value: int) -> None:
        synth = getattr(self, "_synth", None)
        if synth is None:
            return
        cc_method = getattr(synth, "cc", None)
        if callable(cc_method):
            cc_method(int(channel), _clamp_midi_data(control), _clamp_midi_data(value))
            return
        control_change_method = getattr(synth, "control_change", None)
        if callable(control_change_method):
            control_change_method(int(channel), _clamp_midi_data(control), _clamp_midi_data(value))

    def _effective_channel_volume(self, raw_volume: int) -> int:
        return _clamp_midi_data(round(_clamp_midi_data(raw_volume) * self._master_gain))

    def _resolve_channel(self, channel: int | None) -> int:
        if channel is None:
            return self._channel
        return _normalize_channel(channel)


class GsWavetableBackend(SynthBackend):
    def __init__(
        self,
        *,
        output_name: str | None = None,
        channel: int = DEFAULT_MIDI_CHANNEL,
        bank: int = DEFAULT_MIDI_BANK,
        program: int = DEFAULT_MIDI_PROGRAM,
        gain: float = DEFAULT_GAIN,
        fallback_reason: str | None = None,
    ) -> None:
        try:
            self._mido = importlib.import_module("mido")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("未安装 mido / python-rtmidi，无法使用系统 MIDI 输出") from exc

        self._channel = _normalize_channel(channel)
        self._master_gain = 1.0
        self._channel_volumes = {midi_channel: DEFAULT_CHANNEL_VOLUME for midi_channel in range(16)}
        self._output_name = select_midi_output_name(self._mido, preferred_name=output_name)
        if self._output_name is None:
            raise RuntimeError("未找到可用的系统 MIDI 输出")

        try:
            self._port = self._mido.open_output(self._output_name, autoreset=True)
        except TypeError:
            self._port = self._mido.open_output(self._output_name)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"无法打开系统 MIDI 输出: {self._output_name} ({exc})") from exc

        status_message = f"使用系统 MIDI 输出：{self._output_name}"
        if fallback_reason:
            status_message = f"{status_message}（{fallback_reason}）"
        self._status = SynthBackendStatus(
            backend_name="midi_output",
            available=True,
            message=status_message,
            soundfont_path=None,
            output_name=self._output_name,
        )

        try:
            self._select_program(bank=bank, program=program)
            self.set_master_gain(gain)
        except Exception:
            self.close()
            raise

    @property
    def status(self) -> SynthBackendStatus:
        return self._status

    def note_on(self, note: int, velocity: int = 100, *, channel: int | None = None) -> None:
        self._send_message(
            "note_on",
            channel=self._resolve_channel(channel),
            note=_clamp_midi_data(note),
            velocity=_clamp_midi_data(velocity),
        )

    def note_off(self, note: int, *, channel: int | None = None) -> None:
        self._send_message(
            "note_off",
            channel=self._resolve_channel(channel),
            note=_clamp_midi_data(note),
            velocity=0,
        )

    def set_master_gain(self, value: float) -> None:
        self._master_gain = _clamp_gain(value)
        for midi_channel, raw_volume in self._channel_volumes.items():
            self._send_message(
                "control_change",
                channel=midi_channel,
                control=7,
                value=self._effective_channel_volume(raw_volume),
            )

    def set_channel_volume(self, channel: int, value: int) -> None:
        normalized_channel = self._resolve_channel(channel)
        raw_volume = _clamp_midi_data(value)
        self._channel_volumes[normalized_channel] = raw_volume
        self._send_message(
            "control_change",
            channel=normalized_channel,
            control=7,
            value=self._effective_channel_volume(raw_volume),
        )

    def set_channel_pan(self, channel: int, value: int) -> None:
        self._send_message(
            "control_change",
            channel=self._resolve_channel(channel),
            control=10,
            value=_clamp_midi_data(value),
        )

    def set_channel_program(self, channel: int, *, bank: int, program: int) -> None:
        normalized_channel = self._resolve_channel(channel)
        bank_value = effective_midi_bank(normalized_channel, bank)
        bank_msb = max(0, min(127, bank_value // 128))
        bank_lsb = max(0, min(127, bank_value % 128))
        self._send_message("control_change", channel=normalized_channel, control=0, value=bank_msb)
        self._send_message("control_change", channel=normalized_channel, control=32, value=bank_lsb)
        self._send_message("program_change", channel=normalized_channel, program=_clamp_midi_data(program))

    def panic(self) -> None:
        for midi_channel in range(16):
            self._send_message("control_change", channel=midi_channel, control=64, value=0)
            self._send_message("control_change", channel=midi_channel, control=123, value=0)
            self._send_message("control_change", channel=midi_channel, control=120, value=0)
            self._send_message("control_change", channel=midi_channel, control=121, value=0)

    def close(self) -> None:
        port = getattr(self, "_port", None)
        if port is None:
            return
        try:
            self.panic()
        finally:
            try:
                port.close()
            except Exception:  # noqa: BLE001
                pass
            self._port = None

    def _select_program(self, *, bank: int, program: int) -> None:
        self.set_channel_program(self._channel, bank=bank, program=program)

    def _send_message(self, message_type: str, **kwargs) -> None:
        port = getattr(self, "_port", None)
        if port is None:
            return
        message = self._mido.Message(message_type, **kwargs)
        port.send(message)

    def _effective_channel_volume(self, raw_volume: int) -> int:
        return _clamp_midi_data(round(_clamp_midi_data(raw_volume) * self._master_gain))

    def _resolve_channel(self, channel: int | None) -> int:
        if channel is None:
            return self._channel
        return _normalize_channel(channel)


class MidiSynth:
    def __init__(self, backend: SynthBackend) -> None:
        self._backend = backend

    @property
    def status(self) -> SynthBackendStatus:
        return self._backend.status

    @property
    def is_available(self) -> bool:
        return self.status.available

    def note_on(self, note: int, velocity: int = 100, *, channel: int | None = None) -> None:
        self._backend.note_on(note, velocity, channel=channel)

    def note_off(self, note: int, *, channel: int | None = None) -> None:
        self._backend.note_off(note, channel=channel)

    def set_master_gain(self, value: float) -> None:
        self._backend.set_master_gain(value)

    def set_channel_volume(self, channel: int, value: int) -> None:
        self._backend.set_channel_volume(channel, value)

    def set_channel_pan(self, channel: int, value: int) -> None:
        self._backend.set_channel_pan(channel, value)

    def set_channel_program(self, channel: int, *, bank: int, program: int) -> None:
        self._backend.set_channel_program(channel, bank=bank, program=program)

    def apply_channel_config(self, config: MidiChannelConfig) -> None:
        self.set_channel_program(config.channel, bank=config.bank, program=config.program)
        self.set_channel_pan(config.channel, config.pan)
        self.set_channel_volume(config.channel, 0 if config.muted else DEFAULT_CHANNEL_VOLUME)

    def panic(self) -> None:
        self._backend.panic()

    def close(self) -> None:
        self._backend.close()


def iter_soundfont_search_dirs() -> list[Path]:
    workspace_root = Path(__file__).resolve().parents[3]
    cwd = Path.cwd()
    home = Path.home()

    directories = [
        workspace_root / "soundfonts",
        cwd / "soundfonts",
        workspace_root / "assets",
        cwd / "assets",
        home / "soundfonts",
        home / "SoundFonts",
    ]
    return _dedupe_paths(directories)


def discover_soundfonts_in_directory(directory: str | Path) -> list[Path]:
    search_dir = Path(directory).expanduser()
    if not search_dir.exists() or not search_dir.is_dir():
        return []

    candidates: list[Path] = []
    for entry in sorted(search_dir.iterdir(), key=lambda item: item.name.lower()):
        try:
            if entry.suffix.lower() != ".sf2":
                continue
            if not entry.exists() or entry.is_dir():
                continue
            candidates.append(entry.resolve())
        except OSError:
            continue

    candidates.sort(key=lambda item: (_SOUNDFONT_PRIORITY_NAMES.get(item.name.lower(), 100), item.name.lower()))
    return _dedupe_paths(candidates)


def iter_soundfont_candidates(soundfont_path: str | Path | None = None) -> list[Path]:
    if soundfont_path is not None:
        return _expand_soundfont_input(soundfont_path)

    env_value = os.environ.get("SPECTRACER_SOUNDFONT")
    if env_value:
        return _expand_soundfont_input(env_value)

    candidates: list[Path] = []
    for directory in iter_soundfont_search_dirs():
        candidates.extend(discover_soundfonts_in_directory(directory))
    return _dedupe_paths(candidates)


def resolve_soundfont_path(soundfont_path: str | Path | None = None) -> Path | None:
    candidates = iter_soundfont_candidates(soundfont_path)
    return candidates[0] if candidates else None


def iter_midi_output_names(mido_module=None) -> list[str]:
    module = mido_module
    if module is None:
        try:
            module = importlib.import_module("mido")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("未安装 mido / python-rtmidi") from exc

    names = module.get_output_names()
    return [str(name) for name in names]


def select_midi_output_name(mido_module=None, *, preferred_name: str | None = None) -> str | None:
    output_names = iter_midi_output_names(mido_module)
    if not output_names:
        return None

    normalized_preferred = _normalize_output_name(preferred_name)
    if normalized_preferred is None:
        env_output = os.environ.get("SPECTRACER_MIDI_OUT")
        normalized_preferred = _normalize_output_name(env_output)

    if normalized_preferred:
        preferred_lower = normalized_preferred.lower()
        for name in output_names:
            if preferred_lower == name.lower() or preferred_lower in name.lower():
                return name
        raise RuntimeError(f"未找到指定的 MIDI 输出端口: {normalized_preferred}")

    for keyword in _GS_WAVETABLE_KEYWORDS:
        for name in output_names:
            if keyword in name.lower():
                return name

    if len(output_names) == 1:
        return output_names[0]
    return None


def create_default_midi_synth(
    *,
    soundfont_path: str | Path | None = None,
    output_name: str | None = None,
    channel: int = DEFAULT_MIDI_CHANNEL,
    bank: int = DEFAULT_MIDI_BANK,
    program: int = DEFAULT_MIDI_PROGRAM,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    gain: float = DEFAULT_GAIN,
) -> MidiSynth:
    normalized_soundfont_path = _normalize_soundfont_path(soundfont_path)
    resolved_soundfont: Path | None = None
    failure_messages: list[str] = []
    normalized_output_name = _normalize_output_name(output_name)

    if normalized_output_name is not None:
        try:
            backend = GsWavetableBackend(
                output_name=normalized_output_name,
                channel=channel,
                bank=bank,
                program=program,
                gain=gain,
            )
            return MidiSynth(backend)
        except Exception as exc:  # noqa: BLE001
            failure_messages.append(f"指定 MIDI 输出不可用: {exc}")

    if normalized_soundfont_path is not None:
        try:
            resolved_soundfont = resolve_soundfont_path(normalized_soundfont_path)
        except Exception as exc:  # noqa: BLE001
            failure_messages.append(f"指定 SF2 不可用: {exc}")
        else:
            if resolved_soundfont is not None:
                try:
                    backend = FluidSynthBackend(
                        resolved_soundfont,
                        channel=channel,
                        bank=bank,
                        program=program,
                        sample_rate=sample_rate,
                        gain=gain,
                    )
                    return MidiSynth(backend)
                except Exception as exc:  # noqa: BLE001
                    failure_messages.append(f"指定 SF2 的 FluidSynth 不可用: {exc}")
            else:
                failure_messages.append("指定 SF2 不可用")

    if normalized_output_name is None:
        try:
            backend = GsWavetableBackend(
                channel=channel,
                bank=bank,
                program=program,
                gain=gain,
                fallback_reason=failure_messages[-1] if failure_messages else None,
            )
            return MidiSynth(backend)
        except Exception as exc:  # noqa: BLE001
            failure_messages.append(f"系统 MIDI 输出不可用: {exc}")

    if normalized_soundfont_path is None:
        try:
            resolved_soundfont = resolve_soundfont_path()
        except Exception as exc:  # noqa: BLE001
            failure_messages.append(f"SoundFont 检测失败: {exc}")
        else:
            if resolved_soundfont is not None:
                try:
                    backend = FluidSynthBackend(
                        resolved_soundfont,
                        channel=channel,
                        bank=bank,
                        program=program,
                        sample_rate=sample_rate,
                        gain=gain,
                        fallback_reason=failure_messages[-1] if failure_messages else None,
                    )
                    return MidiSynth(backend)
                except Exception as exc:  # noqa: BLE001
                    failure_messages.append(f"FluidSynth 不可用: {exc}")
            else:
                failure_messages.append("未找到可用 SoundFont")

    backend = NullSynthBackend(
        "；".join(failure_messages),
        soundfont_path=resolved_soundfont,
        output_name=normalized_output_name,
    )
    return MidiSynth(backend)


def _expand_soundfont_input(soundfont_path: str | Path) -> list[Path]:
    candidate = Path(soundfont_path).expanduser()
    if candidate.exists() and candidate.is_dir():
        return discover_soundfonts_in_directory(candidate)
    if candidate.exists() and not candidate.is_dir():
        return [candidate.resolve()]
    raise FileNotFoundError(f"SoundFont 不存在: {candidate}")


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        normalized = str(path.expanduser())
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(path)
    return deduped


def _normalize_output_name(output_name: str | None) -> str | None:
    if output_name is None:
        return None
    normalized = str(output_name).strip()
    return normalized or None


def _normalize_soundfont_path(soundfont_path: str | Path | None) -> str | None:
    if soundfont_path is None:
        return None
    normalized = str(soundfont_path).strip()
    return normalized or None


def _normalize_channel(channel: int) -> int:
    return max(0, min(15, int(channel)))


def _clamp_midi_data(value: int) -> int:
    return max(0, min(127, int(value)))


def _clamp_gain(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
