from __future__ import annotations

import importlib


def test_recovery_callbacks_dispatcher_only() -> None:
    mod = importlib.import_module("ccgram.handlers.recovery.recovery_callbacks")
    assert callable(mod.handle_recovery_callback)
    assert callable(mod._clear_recovery_state)
    assert not hasattr(mod, "_validate_recovery_state")

    banner = importlib.import_module("ccgram.handlers.recovery.recovery_banner")
    assert callable(banner._validate_recovery_state)
