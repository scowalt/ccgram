import contextlib
import os
import subprocess
import tempfile
from collections.abc import Iterator

import pytest

_CONFIG_ENV_PREFIXES = ("CCGRAM_",)
_NON_PREFIXED_CONFIG_ENV = (
    "AUTOCLOSE_DEAD_MINUTES",
    "AUTOCLOSE_DONE_MINUTES",
    "CLAUDE_CONFIG_DIR",
    "MONITOR_POLL_INTERVAL",
    "TMUX_SESSION_NAME",
)
for _key in list(os.environ):
    if _key.startswith(_CONFIG_ENV_PREFIXES) or _key in _NON_PREFIXED_CONFIG_ENV:
        del os.environ[_key]

os.environ["TELEGRAM_BOT_TOKEN"] = "test:0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
os.environ["ALLOWED_USERS"] = "12345"
os.environ["CCGRAM_DIR"] = tempfile.mkdtemp(prefix="ccgram-test-")


@pytest.fixture(autouse=True)
def _clear_window_store():
    from ccgram.claude_task_state import claude_task_state
    from ccgram.window_state_store import get_window_store

    def _clear() -> None:
        with contextlib.suppress(RuntimeError):
            get_window_store().window_states.clear()

    claude_task_state.reset()
    _clear()
    yield
    claude_task_state.reset()
    _clear()


@pytest.fixture(autouse=True)
def _wire_multiplexer():
    from ccgram.multiplexer import (
        _reset_multiplexer_for_testing,
        get_multiplexer,
        install_multiplexer,
    )

    install_multiplexer(get_multiplexer("tmux"))
    yield
    _reset_multiplexer_for_testing()


def _close_created_windows(created: list[tuple[str, str]]) -> None:
    closed_workspaces: set[str] = set()
    for backend, window_id in created:
        if backend == "tmux":
            subprocess.run(
                ["tmux", "kill-window", "-t", window_id], capture_output=True
            )
            continue
        subprocess.run(["herdr", "tab", "close", window_id], capture_output=True)
        workspace_id = window_id.split(":")[0]
        if workspace_id in closed_workspaces:
            continue
        closed_workspaces.add(workspace_id)
        subprocess.run(
            ["herdr", "worktree", "remove", "--workspace", workspace_id, "--force"],
            capture_output=True,
        )
        subprocess.run(
            ["herdr", "workspace", "close", workspace_id], capture_output=True
        )


@pytest.fixture(autouse=True)
def _cleanup_created_windows(request, monkeypatch) -> Iterator[None]:
    if not (
        request.node.get_closest_marker("integration")
        or request.node.get_closest_marker("e2e")
    ):
        yield
        return

    created: list[tuple[str, str]] = []

    def _wrap(cls: type, attr: str, backend: str) -> None:
        orig = getattr(cls, attr, None)
        if orig is None:
            return

        async def wrapper(self, *args, **kwargs):  # noqa: ANN001, ANN202
            result = await orig(self, *args, **kwargs)
            if (
                isinstance(result, tuple)
                and len(result) >= 4
                and result[0]
                and isinstance(result[3], str)
                and result[3]
            ):
                created.append((backend, result[3]))
            return result

        monkeypatch.setattr(cls, attr, wrapper)

    from ccgram.multiplexer.herdr import HerdrManager
    from ccgram.multiplexer.tmux import TmuxManager

    _wrap(TmuxManager, "create_window", "tmux")
    _wrap(HerdrManager, "create_window", "herdr")
    _wrap(HerdrManager, "create_worktree_window", "herdr")
    try:
        yield
    finally:
        _close_created_windows(created)
