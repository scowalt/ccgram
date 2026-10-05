import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def _instant_shell_setup_sleep(monkeypatch):
    from ccgram.providers import shell_infra

    monkeypatch.setattr(shell_infra.asyncio, "sleep", AsyncMock())
