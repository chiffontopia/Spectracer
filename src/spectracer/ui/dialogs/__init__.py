"""Qt dialogs."""

from spectracer.ui.dialogs.analysis_options_dialog import AnalysisOptionsDialog
from spectracer.ui.dialogs.channel_config_dialog import ChannelConfigDialog, ChannelConfigDialogResult
from spectracer.ui.dialogs.grid_event_dialog import (
    TempoEventDialog,
    TempoEventDialogResult,
    TimeSignatureEventDialog,
    TimeSignatureEventDialogResult,
)
from spectracer.ui.dialogs.grid_settings_dialog import GridSettingsDialog, GridSettingsDialogResult
from spectracer.ui.dialogs.midi_settings_dialog import MidiSettingsDialog, MidiSettingsDialogResult
from spectracer.ui.dialogs.midi_note_properties_dialog import (
    MidiNotePropertiesDialog,
    MidiNotePropertiesDialogResult,
)

__all__ = [
    "AnalysisOptionsDialog",
    "ChannelConfigDialog",
    "ChannelConfigDialogResult",
    "TempoEventDialog",
    "TempoEventDialogResult",
    "TimeSignatureEventDialog",
    "TimeSignatureEventDialogResult",
    "GridSettingsDialog",
    "GridSettingsDialogResult",
    "MidiSettingsDialog",
    "MidiNotePropertiesDialog",
    "MidiNotePropertiesDialogResult",
    "MidiSettingsDialogResult",
]
