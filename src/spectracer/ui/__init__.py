"""PyQt6 based UI package."""

__all__ = ["SpectracerMainWindow", "launch_ui"]


def __getattr__(name: str):
    if name in {"SpectracerMainWindow", "launch_ui"}:
        from spectracer.ui.main_window import SpectracerMainWindow, launch_ui

        return {"SpectracerMainWindow": SpectracerMainWindow, "launch_ui": launch_ui}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
