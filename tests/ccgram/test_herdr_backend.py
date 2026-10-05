from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import pytest

from ccgram.multiplexer.base import (
    AgentStatus,
    ForegroundInfo,
    MuxEvent,
    PaneDims,
    PaneInfo,
    WindowRef,
)
from ccgram.multiplexer.herdr import (
    HERDR_PROTOCOL_VERSION,
    HERDR_SUPPORTED_PROTOCOLS,
    HerdrError,
    HerdrManager,
)
import ccgram.multiplexer.herdr as herdr_module
from ccgram.multiplexer.herdr_events import SUBSCRIBED, translate_event


PANE_GET = json.dumps(
    {
        "id": "cli:pane:get",
        "result": {
            "pane": {
                "agent": "claude",
                "agent_status": "idle",
                "cwd": "/Users/alexei/Workspace/ccgram",
                "focused": True,
                "foreground_cwd": "/Users/alexei/Workspace/ccgram",
                "pane_id": "w2:p1",
                "tab_id": "w2:t1",
                "terminal_id": "term_abc",
                "title": "ccgram:claude",
                "workspace_id": "w2",
            },
            "type": "pane_info",
        },
    }
)

TAB_GET = json.dumps(
    {
        "id": "cli:tab:get",
        "result": {
            "tab": {
                "label": "herdr-support",
                "tab_id": "w2:t1",
                "workspace_id": "w2",
                "cwd": "/Users/alexei/Workspace/ccgram",
            },
            "type": "tab_info",
        },
    }
)

PANE_LIST_FOR_FIND = json.dumps(
    {
        "id": "cli:pane:list",
        "result": {
            "panes": [
                {
                    "agent": "claude",
                    "agent_status": "idle",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                    "focused": True,
                    "pane_id": "w2:p1",
                    "tab_id": "w2:t1",
                    "workspace_id": "w2",
                },
            ],
            "type": "pane_list",
        },
    }
)

TAB_LIST = json.dumps(
    {
        "id": "cli:tab:list",
        "result": {
            "tabs": [
                {
                    "label": "archfit",
                    "tab_id": "w1:t1",
                    "workspace_id": "w1",
                    "cwd": "/Users/alexei/Workspace/archfit",
                },
                {
                    "label": "ralphex",
                    "tab_id": "w2:t2",
                    "workspace_id": "w2",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                },
            ],
            "type": "tab_list",
        },
    }
)

PANE_LIST = json.dumps(
    {
        "id": "cli:pane:list",
        "result": {
            "panes": [
                {
                    "agent": "claude",
                    "agent_status": "working",
                    "cwd": "/Users/alexei/Workspace/archfit",
                    "focused": True,
                    "pane_id": "w1:p1",
                    "tab_id": "w1:t1",
                    "workspace_id": "w1",
                },
                {
                    "agent_status": "unknown",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                    "focused": False,
                    "pane_id": "w2:p2",
                    "tab_id": "w2:t2",
                    "workspace_id": "w2",
                },
            ],
            "type": "pane_list",
        },
    }
)

WORKSPACE_LIST = json.dumps(
    {
        "id": "cli:workspace:list",
        "result": {
            "workspaces": [
                {
                    "workspace_id": "w1",
                    "label": "archfit",
                    "cwd": "/Users/alexei/Workspace/archfit",
                },
                {
                    "workspace_id": "w2",
                    "label": "ccgram",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                },
            ],
            "type": "workspace_list",
        },
    }
)

PROCESS_INFO = json.dumps(
    {
        "id": "cli:pane:process_info",
        "result": {
            "process_info": {
                "foreground_process_group_id": 40777,
                "foreground_processes": [
                    {
                        "argv": ["python", "-m", "agent"],
                        "cwd": "/Users/alexei/Workspace/ccgram",
                        "name": "python",
                        "pid": 40777,
                    }
                ],
                "pane_id": "w2:p1",
                "shell_pid": 38702,
            },
            "type": "pane_process_info",
        },
    }
)

LAYOUT = json.dumps(
    {
        "id": "cli:pane:layout",
        "result": {
            "layout": {
                "area": {"height": 63, "width": 199, "x": 26, "y": 1},
                "focused_pane_id": "w2:p1",
                "panes": [
                    {
                        "focused": True,
                        "pane_id": "w2:p1",
                        "rect": {"height": 50, "width": 120, "x": 0, "y": 0},
                    }
                ],
                "splits": [],
                "tab_id": "w2:t1",
                "workspace_id": "w2",
                "zoomed": False,
            },
            "type": "pane_layout",
        },
    }
)

TAB_CREATE = json.dumps(
    {
        "id": "cli:tab:create",
        "result": {
            "root_pane": {
                "cwd": "/tmp/work",
                "pane_id": "w2:p9",
                "tab_id": "w2:t9",
                "workspace_id": "w2",
            },
            "tab": {"label": "work", "tab_id": "w2:t9", "workspace_id": "w2"},
            "type": "tab_created",
        },
    }
)

OK = json.dumps({"id": "cli:ok", "result": {"type": "ok"}})

WORKTREE_CREATE = json.dumps(
    {
        "id": "cli:worktree:create",
        "result": {
            "root_pane": {
                "cwd": "/repo.worktrees/ccg-feature",
                "pane_id": "w5:p1",
                "tab_id": "w5:t1",
                "workspace_id": "w5",
            },
            "tab": {"label": "feature", "tab_id": "w5:t1", "workspace_id": "w5"},
            "type": "worktree_created",
            "workspace": {"active_tab_id": "w5:t1", "workspace_id": "w5"},
            "worktree": {
                "branch": "ccg/feature",
                "path": "/repo.worktrees/ccg-feature",
            },
        },
    }
)

WORKTREE_CREATE_THIN = json.dumps(
    {
        "id": "cli:worktree:create",
        "result": {
            "type": "worktree_created",
            "workspace": {"active_tab_id": "w5:t1", "workspace_id": "w5"},
        },
    }
)

PANE_READ_TEXT = "line one\nline two\n"

ERROR_NOT_FOUND = json.dumps(
    {"error": {"code": "pane_not_found", "message": "pane w9:p9 not found"}, "id": "x"}
)


def _status_json(
    protocol: object = HERDR_PROTOCOL_VERSION, running: bool = True
) -> str:
    return json.dumps(
        {
            "client": {"version": "0.7.3", "protocol": protocol},
            "server": {
                "status": "running" if running else "stopped",
                "running": running,
                "version": "0.7.3",
                "protocol": protocol,
                "compatible": True,
            },
            "update": {"restart_needed": False},
        }
    )


class FakeHerdr:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._responses: dict[tuple[str, ...], tuple[int, str, str]] = {}
        self.default: tuple[int, str, str] = (1, "", "no canned response")

    def on(self, *prefix: str, rc: int = 0, out: str = "", err: str = "") -> FakeHerdr:
        self._responses[tuple(prefix)] = (rc, out, err)
        return self

    async def __call__(self, args: Sequence[str]) -> tuple[int, str, str]:
        args = list(args)
        self.calls.append(args)
        best: tuple[tuple[str, ...], tuple[int, str, str]] | None = None
        for key, resp in self._responses.items():
            if list(key) == args[: len(key)] and (
                best is None or len(key) > len(best[0])
            ):
                best = (key, resp)
        return best[1] if best else self.default

    def sent(self, *prefix: str) -> list[str] | None:
        for call in self.calls:
            if call[: len(prefix)] == list(prefix):
                return call
        return None


def _manager(fake: FakeHerdr) -> HerdrManager:
    return HerdrManager(socket_path="/tmp/herdr.sock", runner=fake)


def test_capabilities_are_pinned() -> None:
    caps = HerdrManager().capabilities
    assert caps.name == "herdr"
    assert caps.ids_stable_across_restart is False
    assert caps.exposes_pane_tty is False
    assert caps.native_agent_status is True
    assert caps.read_max_lines == 1000
    assert caps.self_identify_env == "HERDR_PANE_ID"
    assert caps.supports_event_stream is True
    assert caps.native_worktrees is True


def test_constructor_does_no_io() -> None:
    fake = FakeHerdr()
    HerdrManager(socket_path="/tmp/herdr.sock", runner=fake)
    assert fake.calls == []


async def test_subprocess_run_uses_host_spawn_path(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create_subprocess_exec(*argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(
        "ccgram.multiplexer.herdr.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "ccgram.multiplexer.herdr.shutil.which", lambda _: "/opt/bin/herdr"
    )

    rc, out, err = await HerdrManager(socket_path="/tmp/herdr.sock")._subprocess_run(
        ["status"]
    )

    assert (rc, out, err) == (0, "ok", "")
    assert seen["argv"] == ("/opt/bin/herdr", "status")
    kwargs = seen["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["stdout"] is asyncio.subprocess.PIPE
    assert kwargs["stderr"] is asyncio.subprocess.PIPE
    assert kwargs["close_fds"] is False
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["HERDR_SOCKET_PATH"] == "/tmp/herdr.sock"


async def test_subprocess_run_maps_timeout(monkeypatch) -> None:
    class FakeProc:
        returncode = None

        async def communicate(self):
            return b"", b""

        def kill(self) -> None:
            pass

        async def wait(self) -> int:
            return -9

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProc()

    async def fake_wait_for(coro, *, timeout):
        coro.close()
        raise TimeoutError

    monkeypatch.setattr(
        "ccgram.multiplexer.herdr.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("ccgram.multiplexer.herdr.asyncio.wait_for", fake_wait_for)

    rc, out, err = await HerdrManager()._subprocess_run(["status"])

    assert (rc, out, err) == (124, "", "herdr call timed out")


async def test_find_window_by_id_uses_tab_get_and_returns_tab_id() -> None:
    fake = (
        FakeHerdr()
        .on("tab", "get", out=TAB_GET)
        .on("workspace", "list", out=WORKSPACE_LIST)
        .on("pane", "list", out=PANE_LIST_FOR_FIND)
    )
    win = await _manager(fake).find_window_by_id("w2:t1")
    assert win == WindowRef(
        window_id="w2:t1",
        window_name="ccgram ▸ herdr-support",
        cwd="/Users/alexei/Workspace/ccgram",
        pane_current_command="claude",
    )
    assert fake.sent("tab", "get") is not None
    assert fake.sent("pane", "get") is None


async def test_find_window_by_id_returns_none_when_tab_gone() -> None:
    fake = FakeHerdr().on("tab", "get", rc=1, out=ERROR_NOT_FOUND)
    assert await _manager(fake).find_window_by_id("w9:t9") is None


async def test_find_window_by_id_bypasses_internal_label_filter() -> None:
    internal_tab = json.dumps(
        {
            "result": {
                "tab": {
                    "label": "__main__",
                    "tab_id": "w3:t1",
                    "workspace_id": "w3",
                    "cwd": "/tmp",
                },
                "type": "tab_info",
            }
        }
    )
    pane_list_empty = json.dumps({"result": {"panes": [], "type": "pane_list"}})
    workspace_list_empty = json.dumps(
        {"result": {"workspaces": [], "type": "workspace_list"}}
    )
    fake = (
        FakeHerdr()
        .on("tab", "get", out=internal_tab)
        .on("workspace", "list", out=workspace_list_empty)
        .on("pane", "list", out=pane_list_empty)
    )
    win = await _manager(fake).find_window_by_id("w3:t1")
    assert win is not None
    assert win.window_id == "w3:t1"
    assert win.window_name == "__main__"


async def test_reconciliation_listing_returns_none_on_tab_list_failure() -> None:
    failed = _manager(FakeHerdr())
    empty = _manager(
        FakeHerdr().on(
            "tab",
            "list",
            out=json.dumps({"result": {"tabs": [], "type": "tab_list"}}),
        )
    )

    assert await failed.list_windows_for_reconciliation() is None
    assert await failed.list_windows() == []
    assert await empty.list_windows_for_reconciliation() == []


async def test_list_windows_returns_one_ref_per_tab() -> None:
    fake = (
        FakeHerdr()
        .on("tab", "list", out=TAB_LIST)
        .on("pane", "list", out=PANE_LIST)
        .on("workspace", "list", out=WORKSPACE_LIST)
    )
    wins = await _manager(fake).list_windows()
    ids = {w.window_id: w for w in wins}
    assert set(ids) == {"w1:t1", "w2:t2"}
    assert ids["w1:t1"].pane_current_command == "claude"
    assert ids["w1:t1"].window_name == "archfit ▸ archfit"
    assert ids["w2:t2"].pane_current_command == ""
    assert ids["w2:t2"].window_name == "ccgram ▸ ralphex"


async def test_list_windows_uses_focused_pane_as_representative() -> None:
    split_tab_list = json.dumps(
        {
            "result": {
                "tabs": [
                    {
                        "label": "feature",
                        "tab_id": "w2:t1",
                        "workspace_id": "w2",
                        "cwd": "/Users/alexei/Workspace/ccgram",
                    }
                ],
                "type": "tab_list",
            }
        }
    )
    split_pane_list = json.dumps(
        {
            "result": {
                "panes": [
                    {
                        "agent": "codex",
                        "cwd": "/Users/alexei/Workspace/ccgram",
                        "pane_id": "w2:p1",
                        "tab_id": "w2:t1",
                        "workspace_id": "w2",
                        "focused": False,
                    },
                    {
                        "agent": "claude",
                        "cwd": "/Users/alexei/Workspace/ccgram",
                        "pane_id": "w2:p2",
                        "tab_id": "w2:t1",
                        "workspace_id": "w2",
                        "focused": True,
                    },
                ],
                "type": "pane_list",
            }
        }
    )
    fake = (
        FakeHerdr()
        .on("tab", "list", out=split_tab_list)
        .on("pane", "list", out=split_pane_list)
        .on("workspace", "list", out=WORKSPACE_LIST)
    )
    wins = await _manager(fake).list_windows()
    assert len(wins) == 1
    win = wins[0]
    assert win.window_id == "w2:t1"
    assert win.pane_current_command == "claude"
    assert win.window_name == "ccgram ▸ feature"


async def test_list_windows_filters_internal_workspace_label() -> None:
    tab_list = json.dumps(
        {
            "result": {
                "tabs": [
                    {
                        "label": "normal",
                        "tab_id": "w1:t1",
                        "workspace_id": "w1",
                        "cwd": "/a",
                    },
                    {
                        "label": "agent",
                        "tab_id": "w2:t1",
                        "workspace_id": "w2",
                        "cwd": "/b",
                    },
                ],
                "type": "tab_list",
            }
        }
    )
    workspace_list = json.dumps(
        {
            "result": {
                "workspaces": [
                    {"workspace_id": "w1", "label": "myproject", "cwd": "/a"},
                    {"workspace_id": "w2", "label": "__main__", "cwd": "/b"},
                ],
                "type": "workspace_list",
            }
        }
    )
    pane_list_empty = json.dumps({"result": {"panes": [], "type": "pane_list"}})
    fake = (
        FakeHerdr()
        .on("tab", "list", out=tab_list)
        .on("pane", "list", out=pane_list_empty)
        .on("workspace", "list", out=workspace_list)
    )
    wins = await _manager(fake).list_windows()
    ids = {w.window_id for w in wins}
    assert "w1:t1" in ids
    assert "w2:t1" not in ids


async def test_list_windows_filters_internal_tab_label() -> None:
    tab_list = json.dumps(
        {
            "result": {
                "tabs": [
                    {
                        "label": "normal",
                        "tab_id": "w1:t1",
                        "workspace_id": "w1",
                        "cwd": "/a",
                    },
                    {
                        "label": "__internal__",
                        "tab_id": "w1:t2",
                        "workspace_id": "w1",
                        "cwd": "/a",
                    },
                ],
                "type": "tab_list",
            }
        }
    )
    workspace_list = json.dumps(
        {
            "result": {
                "workspaces": [
                    {"workspace_id": "w1", "label": "myproject", "cwd": "/a"},
                ],
                "type": "workspace_list",
            }
        }
    )
    pane_list_empty = json.dumps({"result": {"panes": [], "type": "pane_list"}})
    fake = (
        FakeHerdr()
        .on("tab", "list", out=tab_list)
        .on("pane", "list", out=pane_list_empty)
        .on("workspace", "list", out=workspace_list)
    )
    wins = await _manager(fake).list_windows()
    ids = {w.window_id for w in wins}
    assert "w1:t1" in ids
    assert "w1:t2" not in ids


async def test_list_windows_renders_adaptive_labels() -> None:
    fake = (
        FakeHerdr()
        .on("tab", "list", out=TAB_LIST)
        .on("pane", "list", out=PANE_LIST)
        .on("workspace", "list", out=WORKSPACE_LIST)
    )
    wins = await _manager(fake).list_windows()
    ids = {w.window_id: w for w in wins}
    assert ids["w1:t1"].window_name == "archfit ▸ archfit"
    assert ids["w1:t1"].pane_current_command == "claude"
    assert ids["w2:t2"].window_name == "ccgram ▸ ralphex"


_SPLIT_TABS = json.dumps(
    {
        "result": {
            "tabs": [
                {
                    "label": "feature",
                    "tab_id": "w2:t1",
                    "workspace_id": "w2",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                }
            ],
            "type": "tab_list",
        }
    }
)

_SPLIT_PANES = json.dumps(
    {
        "result": {
            "panes": [
                {
                    "agent": "claude",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                    "pane_id": "w2:p1",
                    "tab_id": "w2:t1",
                    "workspace_id": "w2",
                    "focused": True,
                },
                {
                    "agent": "codex",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                    "pane_id": "w2:p2",
                    "tab_id": "w2:t1",
                    "workspace_id": "w2",
                    "focused": False,
                },
            ],
            "type": "pane_list",
        }
    }
)


async def test_list_windows_split_tab_produces_one_ref_with_tab_suffix() -> None:
    fake = (
        FakeHerdr()
        .on("tab", "list", out=_SPLIT_TABS)
        .on("pane", "list", out=_SPLIT_PANES)
        .on("workspace", "list", out=WORKSPACE_LIST)
    )
    wins = await _manager(fake).list_windows()
    assert len(wins) == 1
    win = wins[0]
    assert win.window_id == "w2:t1"
    assert win.window_name == "ccgram ▸ feature"


async def test_workspace_rename_relabels_without_changing_tab_id() -> None:
    before = {
        w.window_id: w.window_name
        for w in await _manager(
            FakeHerdr()
            .on("tab", "list", out=TAB_LIST)
            .on("pane", "list", out=PANE_LIST)
            .on("workspace", "list", out=WORKSPACE_LIST)
        ).list_windows()
    }
    renamed_ws = json.dumps(
        {
            "result": {
                "workspaces": [
                    {"workspace_id": "w1", "label": "archfit-v2", "cwd": "/a"},
                    {"workspace_id": "w2", "label": "ccgram", "cwd": "/b"},
                ],
                "type": "workspace_list",
            }
        }
    )
    after = {
        w.window_id: w.window_name
        for w in await _manager(
            FakeHerdr()
            .on("tab", "list", out=TAB_LIST)
            .on("pane", "list", out=PANE_LIST)
            .on("workspace", "list", out=renamed_ws)
        ).list_windows()
    }
    assert before["w1:t1"] == "archfit ▸ archfit"
    assert after["w1:t1"] == "archfit-v2 ▸ archfit"
    assert set(after) == set(before)


async def test_kill_window_uses_tab_close() -> None:
    fake = FakeHerdr().on("tab", "close", out=OK)
    assert await _manager(fake).kill_window("w2:t1") is True
    assert fake.sent("tab", "close") == ["tab", "close", "w2:t1"]
    assert fake.sent("pane", "close") is None


async def test_rename_window_uses_tab_rename() -> None:
    fake = FakeHerdr().on("tab", "rename", out=OK)
    assert await _manager(fake).rename_window("w2:t1", "newname") is True
    assert fake.sent("tab", "rename") == ["tab", "rename", "w2:t1", "newname"]
    assert fake.sent("pane", "rename") is None


async def test_create_window_returns_tab_id_and_launches(tmp_path) -> None:
    fake = FakeHerdr().on("tab", "create", out=TAB_CREATE).on("pane", "run", out=OK)
    ok, msg, name, win_id = await _manager(fake).create_window(
        str(tmp_path),
        window_name="work",
        launch_command="claude",
        agent_args="--continue",
    )
    assert ok is True
    assert win_id == "w2:t9"
    assert name == "work"
    assert str(tmp_path) in msg
    assert fake.sent("pane", "run") == ["pane", "run", "w2:p9", "claude --continue"]


async def test_create_window_rejects_missing_directory() -> None:
    fake = FakeHerdr()
    ok, msg, _name, win_id = await _manager(fake).create_window("/no/such/dir")
    assert ok is False
    assert "does not exist" in msg
    assert win_id == ""
    assert fake.calls == []


async def test_create_window_reuses_matching_workspace(tmp_path) -> None:
    ws_list = json.dumps(
        {
            "result": {
                "workspaces": [
                    {"workspace_id": "w5", "label": "repo", "cwd": str(tmp_path)}
                ],
                "type": "workspace_list",
            }
        }
    )
    fake = (
        FakeHerdr()
        .on("workspace", "list", out=ws_list)
        .on("tab", "create", out=TAB_CREATE)
        .on("pane", "run", out=OK)
    )
    ok, _msg, _name, win_id = await _manager(fake).create_window(
        str(tmp_path), launch_command="claude"
    )
    assert ok is True
    assert win_id == "w2:t9"
    assert fake.sent("workspace", "create") is None
    tab_call = fake.sent("tab", "create")
    assert tab_call is not None
    assert "--workspace" in tab_call and "w5" in tab_call


async def test_create_window_creates_workspace_when_absent(tmp_path) -> None:
    ws_list = json.dumps({"result": {"workspaces": [], "type": "workspace_list"}})
    ws_create = json.dumps(
        {
            "result": {
                "workspace": {"workspace_id": "w7", "cwd": str(tmp_path)},
                "type": "workspace_created",
            }
        }
    )
    fake = (
        FakeHerdr()
        .on("workspace", "list", out=ws_list)
        .on("workspace", "create", out=ws_create)
        .on("tab", "create", out=TAB_CREATE)
        .on("pane", "run", out=OK)
    )
    ok, _msg, _name, win_id = await _manager(fake).create_window(
        str(tmp_path), launch_command="claude"
    )
    assert ok is True
    assert win_id == "w2:t9"
    create_call = fake.sent("workspace", "create")
    assert create_call is not None and "--cwd" in create_call
    tab_call = fake.sent("tab", "create")
    assert tab_call is not None
    assert "--workspace" in tab_call and "w7" in tab_call


async def test_create_window_falls_back_when_no_workspace_support(tmp_path) -> None:
    fake = FakeHerdr().on("tab", "create", out=TAB_CREATE).on("pane", "run", out=OK)
    ok, _msg, _name, win_id = await _manager(fake).create_window(
        str(tmp_path), launch_command="claude"
    )
    assert ok is True
    assert win_id == "w2:t9"
    tab_call = fake.sent("tab", "create")
    assert tab_call is not None
    assert "--workspace" not in tab_call


async def test_create_worktree_window_delegates_and_launches(tmp_path) -> None:
    fake = (
        FakeHerdr()
        .on("worktree", "create", out=WORKTREE_CREATE)
        .on("pane", "run", out=OK)
    )
    ok, msg, name, win_id = await _manager(fake).create_worktree_window(
        str(tmp_path),
        "/repo.worktrees/ccg-feature",
        "ccg/feature",
        window_name="feature",
        launch_command="claude",
    )
    assert ok is True
    assert win_id == "w5:t1"
    assert name == "feature"
    assert "ccg/feature" in msg
    create_call = fake.sent("worktree", "create")
    assert create_call is not None
    assert create_call[:2] == ["worktree", "create"]
    for flag, value in (
        ("--cwd", str(tmp_path)),
        ("--branch", "ccg/feature"),
        ("--path", "/repo.worktrees/ccg-feature"),
        ("--label", "feature"),
    ):
        assert flag in create_call and create_call[create_call.index(flag) + 1] == value
    assert "--no-focus" in create_call
    assert fake.sent("pane", "run") == ["pane", "run", "w5:p1", "claude"]


async def test_create_worktree_window_rejects_missing_repo() -> None:
    fake = FakeHerdr()
    ok, msg, _name, win_id = await _manager(fake).create_worktree_window(
        "/no/such/repo", "/no/such/repo.worktrees/x", "ccg/x"
    )
    assert ok is False
    assert "not a directory" in msg.lower()
    assert win_id == ""
    assert fake.calls == []


async def test_create_worktree_window_resolves_tab_from_workspace(tmp_path) -> None:
    fake = FakeHerdr().on("worktree", "create", out=WORKTREE_CREATE_THIN)
    ok, _msg, _name, win_id = await _manager(fake).create_worktree_window(
        str(tmp_path), "/wt", "ccg/x"
    )
    assert ok is True
    assert win_id == "w5:t1"


async def test_create_window_fails_and_closes_tab_when_root_pane_missing(
    tmp_path,
) -> None:
    created_without_root = json.dumps(
        {
            "result": {
                "tab": {"label": "work", "tab_id": "w2:t9", "workspace_id": "w2"},
                "type": "tab_created",
            }
        }
    )
    fake = (
        FakeHerdr()
        .on("tab", "create", out=created_without_root)
        .on("tab", "close", out=OK)
    )

    ok, msg, _name, win_id = await _manager(fake).create_window(
        str(tmp_path), launch_command="claude"
    )

    assert ok is False
    assert "root pane" in msg
    assert win_id == ""
    assert fake.sent("pane", "run") is None
    assert fake.sent("tab", "close") == ["tab", "close", "w2:t9"]


async def test_create_window_fails_and_closes_tab_when_agent_launch_fails(
    tmp_path,
) -> None:
    fake = (
        FakeHerdr()
        .on("tab", "create", out=TAB_CREATE)
        .on("pane", "run", rc=1, err="launch failed")
        .on("tab", "close", out=OK)
    )

    ok, msg, _name, win_id = await _manager(fake).create_window(
        str(tmp_path), launch_command="claude"
    )

    assert ok is False
    assert "Failed to launch" in msg
    assert win_id == ""
    assert fake.sent("tab", "close") == ["tab", "close", "w2:t9"]


PANE_LIST_SINGLE = json.dumps(
    {
        "id": "cli:pane:list",
        "result": {
            "panes": [
                {
                    "agent": "claude",
                    "agent_status": "idle",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                    "focused": True,
                    "pane_id": "w2:p1",
                    "tab_id": "w2:t1",
                    "workspace_id": "w2",
                },
            ],
            "type": "pane_list",
        },
    }
)

PANE_LIST_SPLIT = json.dumps(
    {
        "id": "cli:pane:list",
        "result": {
            "panes": [
                {
                    "agent": "claude",
                    "agent_status": "working",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                    "focused": True,
                    "pane_id": "w2:p1",
                    "tab_id": "w2:t1",
                    "workspace_id": "w2",
                },
                {
                    "agent": "codex",
                    "agent_status": "idle",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                    "focused": False,
                    "pane_id": "w2:p2",
                    "tab_id": "w2:t1",
                    "workspace_id": "w2",
                },
            ],
            "type": "pane_list",
        },
    }
)

PANE_LIST_SPLIT_NO_FOCUS = json.dumps(
    {
        "id": "cli:pane:list",
        "result": {
            "panes": [
                {
                    "agent": "claude",
                    "agent_status": "idle",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                    "focused": False,
                    "pane_id": "w2:p1",
                    "tab_id": "w2:t1",
                    "workspace_id": "w2",
                },
                {
                    "agent": "codex",
                    "agent_status": "idle",
                    "cwd": "/Users/alexei/Workspace/ccgram",
                    "focused": False,
                    "pane_id": "w2:p2",
                    "tab_id": "w2:t1",
                    "workspace_id": "w2",
                },
            ],
            "type": "pane_list",
        },
    }
)

LAYOUT_SPLIT = json.dumps(
    {
        "id": "cli:pane:layout",
        "result": {
            "layout": {
                "area": {"height": 63, "width": 199, "x": 26, "y": 1},
                "focused_pane_id": "w2:p1",
                "panes": [
                    {
                        "focused": True,
                        "pane_id": "w2:p1",
                        "rect": {"height": 50, "width": 120, "x": 0, "y": 0},
                    },
                    {
                        "focused": False,
                        "pane_id": "w2:p2",
                        "rect": {"height": 50, "width": 79, "x": 120, "y": 0},
                    },
                ],
                "splits": [],
                "tab_id": "w2:t1",
                "workspace_id": "w2",
                "zoomed": False,
            },
            "type": "pane_layout",
        },
    }
)


async def test_active_pane_returns_focused_pane() -> None:
    fake = FakeHerdr().on("pane", "list", out=PANE_LIST_SPLIT)
    pane_id = await _manager(fake)._active_pane("w2:t1")
    assert pane_id == "w2:p1"


async def test_active_pane_returns_first_when_no_focused() -> None:
    fake = FakeHerdr().on("pane", "list", out=PANE_LIST_SPLIT_NO_FOCUS)
    pane_id = await _manager(fake)._active_pane("w2:t1")
    assert pane_id == "w2:p1"


async def test_active_pane_returns_none_for_empty_tab() -> None:
    empty = json.dumps({"result": {"panes": [], "type": "pane_list"}})
    fake = FakeHerdr().on("pane", "list", out=empty)
    assert await _manager(fake)._active_pane("w2:t1") is None


async def test_agent_status_maps_active_pane_state() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "get", out=PANE_GET)
    )
    status = await _manager(fake).agent_status("w2:t1")
    assert status == AgentStatus(state="idle", agent="claude", custom_status="")


async def test_agent_status_reads_focused_pane_in_split_tab() -> None:
    pane_get_working = json.dumps(
        {
            "result": {
                "pane": {
                    "agent": "claude",
                    "agent_status": "working",
                    "custom_status": "running tests",
                    "pane_id": "w2:p1",
                    "tab_id": "w2:t1",
                },
                "type": "pane_info",
            }
        }
    )
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SPLIT)
        .on("pane", "get", out=pane_get_working)
    )
    status = await _manager(fake).agent_status("w2:t1")
    assert status == AgentStatus(
        state="working", agent="claude", custom_status="running tests"
    )
    assert fake.sent("pane", "get", "w2:p1") is not None


async def test_agent_status_none_for_empty_tab() -> None:
    empty = json.dumps({"result": {"panes": [], "type": "pane_list"}})
    fake = FakeHerdr().on("pane", "list", out=empty)
    assert await _manager(fake).agent_status("w2:t1") is None


async def test_agent_status_none_when_status_field_absent() -> None:
    pane_get_no_status = json.dumps(
        {
            "result": {
                "pane": {"pane_id": "w2:p1", "tab_id": "w2:t1"},
                "type": "pane_info",
            }
        }
    )
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "get", out=pane_get_no_status)
    )
    assert await _manager(fake).agent_status("w2:t1") is None


async def test_split_window_returns_new_pane_id() -> None:
    split_out = json.dumps(
        {
            "result": {
                "pane": {"pane_id": "w2:p2", "tab_id": "w2:t1"},
                "type": "pane_info",
            }
        }
    )
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "split", out=split_out)
    )
    new_id = await _manager(fake).split_window("w2:t1")
    assert new_id == "w2:p2"
    assert fake.sent("pane", "split") == [
        "pane",
        "split",
        "w2:p1",
        "--direction",
        "down",
        "--no-focus",
    ]


async def test_split_window_none_for_empty_tab() -> None:
    empty = json.dumps({"result": {"panes": [], "type": "pane_list"}})
    fake = FakeHerdr().on("pane", "list", out=empty)
    assert await _manager(fake).split_window("w2:t1") is None


async def test_split_window_none_on_split_failure() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "split", rc=1, err="boom")
    )
    assert await _manager(fake).split_window("w2:t1") is None


async def test_active_pane_single_tab_returns_that_pane() -> None:
    fake = FakeHerdr().on("pane", "list", out=PANE_LIST_SINGLE)
    pane_id = await _manager(fake)._active_pane("w2:t1")
    assert pane_id == "w2:p1"


async def test_foreground_from_process_info() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "process-info", out=PROCESS_INFO)
    )
    fg = await _manager(fake).foreground("w2:t1")
    assert fg == ForegroundInfo(
        pid=40777,
        pgid=40777,
        argv=["python", "-m", "agent"],
        cwd="/Users/alexei/Workspace/ccgram",
        tty="",
    )
    assert fake.sent("pane", "list") is not None
    pi_call = fake.sent("pane", "process-info")
    assert pi_call is not None and "w2:p1" in pi_call


async def test_foreground_split_tab_uses_focused_pane() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SPLIT)
        .on("pane", "process-info", out=PROCESS_INFO)
    )
    fg = await _manager(fake).foreground("w2:t1")
    assert fg is not None
    pi_call = fake.sent("pane", "process-info")
    assert pi_call is not None and "w2:p1" in pi_call


async def test_pane_dims_from_layout() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "layout", out=LAYOUT)
    )
    dims = await _manager(fake).pane_dims("w2:t1")
    assert dims == PaneDims(width=120, height=50)
    layout_call = fake.sent("pane", "layout")
    assert layout_call is not None and "w2:p1" in layout_call


async def test_list_panes_returns_single_pane() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "layout", out=LAYOUT)
    )
    panes = await _manager(fake).list_panes("w2:t1")
    assert panes == [
        PaneInfo(
            pane_id="w2:p1",
            index=1,
            active=True,
            command="claude",
            path="/Users/alexei/Workspace/ccgram",
            width=120,
            height=50,
        )
    ]


async def test_list_panes_returns_all_panes_in_split_tab() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SPLIT)
        .on("pane", "layout", out=LAYOUT_SPLIT)
    )
    panes = await _manager(fake).list_panes("w2:t1")
    assert len(panes) == 2
    pane_ids = {p.pane_id for p in panes}
    assert pane_ids == {"w2:p1", "w2:p2"}
    p1 = next(p for p in panes if p.pane_id == "w2:p1")
    assert p1.active is True
    assert p1.command == "claude"
    assert p1.width == 120
    p2 = next(p for p in panes if p.pane_id == "w2:p2")
    assert p2.active is False
    assert p2.command == "codex"
    assert p2.width == 79


async def test_list_panes_returns_empty_for_empty_tab() -> None:
    empty = json.dumps({"result": {"panes": [], "type": "pane_list"}})
    fake = FakeHerdr().on("pane", "list", out=empty)
    assert await _manager(fake).list_panes("w2:t1") == []


async def test_capture_pane_returns_text() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "read", out=PANE_READ_TEXT)
    )
    res = await _manager(fake).capture_pane("w2:t1")
    assert res == "line one\nline two"
    call = fake.sent("pane", "read")
    assert call is not None
    assert "--source" in call and "visible" in call
    assert "text" in call
    assert "w2:p1" in call


async def test_capture_pane_ansi_requests_ansi_format() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "read", out=PANE_READ_TEXT)
    )
    await _manager(fake).capture_pane("w2:t1", with_ansi=True)
    call = fake.sent("pane", "read")
    assert call is not None and "ansi" in call


async def test_capture_pane_returns_none_when_tab_has_no_panes() -> None:
    empty = json.dumps({"result": {"panes": [], "type": "pane_list"}})
    fake = FakeHerdr().on("pane", "list", out=empty)
    assert await _manager(fake).capture_pane("w2:t1") is None


async def test_scrollback_clamps_to_read_max_lines_and_flags_truncated() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "read", out=PANE_READ_TEXT)
    )
    res = await _manager(fake).capture_scrollback("w2:t1", lines=5000)
    assert res is not None
    assert res.truncated is True
    call = fake.sent("pane", "read")
    assert call is not None
    assert "1000" in call
    assert "5000" not in call
    assert "w2:p1" in call


async def test_scrollback_under_cap_is_not_truncated() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "read", out=PANE_READ_TEXT)
    )
    res = await _manager(fake).capture_scrollback("w2:t1", lines=200)
    assert res is not None and res.truncated is False
    call = fake.sent("pane", "read")
    assert call is not None and "200" in call


async def test_send_literal_enter_uses_pane_run() -> None:
    fake = (
        FakeHerdr().on("pane", "list", out=PANE_LIST_SINGLE).on("pane", "run", out=OK)
    )
    assert await _manager(fake).send("w2:t1", "hello world") is True
    assert fake.sent("pane", "run") == ["pane", "run", "w2:p1", "hello world"]


async def test_send_no_enter_uses_send_text() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "send-text", out=OK)
    )
    assert await _manager(fake).send("w2:t1", "draft", enter=False) is True
    assert fake.sent("pane", "send-text") == ["pane", "send-text", "w2:p1", "draft"]


async def test_send_special_keys_uses_send_keys() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "send-keys", out=OK)
    )
    assert (
        await _manager(fake).send("w2:t1", "Down", enter=False, literal=False) is True
    )
    assert fake.sent("pane", "send-keys") == ["pane", "send-keys", "w2:p1", "Down"]


async def test_send_keys_appends_enter_when_requested() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "send-keys", out=OK)
    )
    await _manager(fake).send("w2:t1", "", enter=True, literal=False)
    assert fake.sent("pane", "send-keys") == ["pane", "send-keys", "w2:p1", "Enter"]


async def test_send_returns_false_when_tab_has_no_panes() -> None:
    empty = json.dumps({"result": {"panes": [], "type": "pane_list"}})
    fake = FakeHerdr().on("pane", "list", out=empty)
    assert await _manager(fake).send("w2:t1", "hello") is False


async def test_send_split_tab_targets_focused_pane() -> None:
    fake = FakeHerdr().on("pane", "list", out=PANE_LIST_SPLIT).on("pane", "run", out=OK)
    assert await _manager(fake).send("w2:t1", "go") is True
    assert fake.sent("pane", "run") == ["pane", "run", "w2:p1", "go"]


async def test_send_to_pane_bypasses_tab_resolution() -> None:
    fake = FakeHerdr().on("pane", "run", out=OK)
    assert await _manager(fake).send_to_pane("w2:p2", "msg") is True
    assert fake.sent("pane", "run") == ["pane", "run", "w2:p2", "msg"]
    assert fake.sent("pane", "list") is None


async def test_send_to_pane_rejects_pane_outside_window_scope() -> None:
    fake = (
        FakeHerdr().on("pane", "list", out=PANE_LIST_SINGLE).on("pane", "run", out=OK)
    )

    assert await _manager(fake).send_to_pane("w9:p9", "msg", window_id="w2:t1") is False

    assert fake.sent("pane", "run") is None


async def test_send_to_pane_allows_pane_inside_window_scope() -> None:
    fake = FakeHerdr().on("pane", "list", out=PANE_LIST_SPLIT).on("pane", "run", out=OK)

    assert await _manager(fake).send_to_pane("w2:p2", "msg", window_id="w2:t1") is True

    assert fake.sent("pane", "run") == ["pane", "run", "w2:p2", "msg"]


async def test_capture_pane_by_id_rejects_pane_outside_window_scope() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "read", out=PANE_READ_TEXT)
    )

    assert await _manager(fake).capture_pane_by_id("w9:p9", window_id="w2:t1") is None

    assert fake.sent("pane", "read") is None


async def test_capture_pane_by_id_allows_pane_inside_window_scope() -> None:
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SPLIT)
        .on("pane", "read", out=PANE_READ_TEXT)
    )

    assert (
        await _manager(fake).capture_pane_by_id("w2:p2", window_id="w2:t1")
        == "line one\nline two"
    )

    assert fake.sent("pane", "read") is not None


async def test_list_windows_reuses_last_good_refs_on_transient_scan_failure() -> None:
    fake = (
        FakeHerdr()
        .on("tab", "list", out=TAB_LIST)
        .on("pane", "list", out=PANE_LIST)
        .on("workspace", "list", out=WORKSPACE_LIST)
    )
    mgr = _manager(fake)
    first = await mgr.list_windows()

    fake.on("tab", "list", rc=1, err="socket down")
    second = await mgr.list_windows()

    assert second == first
    assert mgr.last_window_scan_failed is True


async def test_list_windows_reuses_last_good_refs_on_partial_scan_failure() -> None:
    fake = (
        FakeHerdr()
        .on("tab", "list", out=TAB_LIST)
        .on("pane", "list", out=PANE_LIST)
        .on("workspace", "list", out=WORKSPACE_LIST)
    )
    mgr = _manager(fake)
    first = await mgr.list_windows()

    fake.on("workspace", "list", rc=1, err="socket down")
    second = await mgr.list_windows()

    assert second == first
    assert mgr.last_window_scan_failed is True


async def test_list_windows_uses_unlabeled_tabs_when_initial_workspace_scan_fails() -> (
    None
):
    fake = (
        FakeHerdr()
        .on("tab", "list", out=TAB_LIST)
        .on("pane", "list", out=PANE_LIST)
        .on("workspace", "list", rc=1, err="unknown command")
    )
    refs = await _manager(fake).list_windows()

    assert [ref.window_id for ref in refs] == ["w1:t1", "w2:t2"]
    assert [ref.window_name for ref in refs] == ["archfit", "ralphex"]


async def test_list_windows_cold_pane_scan_failure_is_not_authoritative() -> None:
    fake = (
        FakeHerdr()
        .on("tab", "list", out=TAB_LIST)
        .on("pane", "list", rc=1, err="socket down")
        .on("workspace", "list", out=WORKSPACE_LIST)
    )
    mgr = _manager(fake)

    assert await mgr.list_windows() == []
    assert mgr.last_window_scan_failed is True


async def test_list_windows_does_not_cache_partial_pane_scan() -> None:
    fake = (
        FakeHerdr()
        .on("tab", "list", out=TAB_LIST)
        .on("pane", "list", out=PANE_LIST)
        .on("workspace", "list", out=WORKSPACE_LIST)
    )
    mgr = _manager(fake)
    first = await mgr.list_windows()

    fake.on("pane", "list", rc=1, err="socket down")
    assert await mgr.list_windows() == first
    assert mgr.last_window_scan_failed is True

    fake.on("pane", "list", out=PANE_LIST)
    assert await mgr.list_windows() == first
    assert mgr.last_window_scan_failed is False


async def test_subprocess_runner_kills_child_on_cancellation(monkeypatch) -> None:
    class HangingProc:
        returncode = None

        def __init__(self) -> None:
            self.killed = False
            self.waited = False

        async def communicate(self):
            await asyncio.Future()

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.waited = True
            return -9

    proc = HangingProc()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    task = asyncio.create_task(HerdrManager(binary="herdr")._subprocess_run(["status"]))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed is True
    assert proc.waited is True


async def test_socket_down_returns_none_not_crash() -> None:
    fake = FakeHerdr().on("tab", "get", rc=127, err="connection refused")
    mgr = _manager(fake)
    assert await mgr.find_window_by_id("w2:t1") is None
    assert await mgr.capture_pane("w2:t1") is None


async def test_bad_id_error_payload_returns_none() -> None:
    fake = FakeHerdr().on("tab", "get", rc=1, out=ERROR_NOT_FOUND)
    assert await _manager(fake).find_window_by_id("w9:t9") is None


async def test_foreground_missing_process_returns_none() -> None:
    empty_proc = json.dumps(
        {
            "result": {
                "process_info": {
                    "foreground_process_group_id": 0,
                    "foreground_processes": [],
                    "pane_id": "w2:p1",
                },
                "type": "pane_process_info",
            }
        }
    )
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "process-info", out=empty_proc)
    )
    assert await _manager(fake).foreground("w2:t1") is None


@pytest.mark.parametrize("protocol", sorted(HERDR_SUPPORTED_PROTOCOLS))
async def test_ensure_session_accepts_supported_protocol(protocol: int) -> None:
    fake = FakeHerdr().on("status", out=_status_json(protocol=protocol))
    await _manager(fake).ensure_session()
    assert fake.sent("status") is not None


async def test_ensure_session_raises_on_non_json_status() -> None:
    fake = FakeHerdr().on("status", out="not json {{{")
    with pytest.raises(HerdrError, match="non-JSON"):
        await _manager(fake).ensure_session()


async def test_ensure_session_raises_on_non_object_json_status() -> None:
    fake = FakeHerdr().on("status", out="[]")
    with pytest.raises(HerdrError, match="non-object JSON"):
        await _manager(fake).ensure_session()


@pytest.mark.parametrize("protocol", [13, 17, "17", None, []])
async def test_ensure_session_warns_and_continues_for_unverified_protocol(
    protocol: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        herdr_module.logger,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    fake = FakeHerdr().on("status", out=_status_json(protocol=protocol))
    await _manager(fake).ensure_session()

    assert warnings == [
        (
            ("herdr protocol is unverified; continuing",),
            {
                "server_protocol": protocol,
                "supported_protocols": sorted(HERDR_SUPPORTED_PROTOCOLS),
                "cli_server_compatible": True,
            },
        )
    ]


async def test_ensure_session_warns_and_continues_when_cli_is_incompatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        herdr_module.logger,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    status = json.loads(_status_json())
    status["server"]["compatible"] = False

    fake = FakeHerdr().on("status", out=json.dumps(status))
    await _manager(fake).ensure_session()

    assert warnings == [
        (
            ("herdr protocol is unverified; continuing",),
            {
                "server_protocol": HERDR_PROTOCOL_VERSION,
                "supported_protocols": sorted(HERDR_SUPPORTED_PROTOCOLS),
                "cli_server_compatible": False,
            },
        )
    ]


async def test_ensure_session_raises_when_socket_down() -> None:
    fake = FakeHerdr().on("status", rc=127, err="connection refused")
    with pytest.raises(HerdrError, match="status failed"):
        await _manager(fake).ensure_session()


async def test_ensure_session_raises_when_server_not_running() -> None:
    fake = FakeHerdr().on("status", out=_status_json(running=False))
    with pytest.raises(HerdrError, match="not running"):
        await _manager(fake).ensure_session()


def test_translate_event_maps_and_filters() -> None:
    p2w = {"w2:p1": "w2:t1"}
    assert translate_event(
        {
            "event": "pane.agent_status_changed",
            "data": {"pane_id": "w2:p1", "agent_status": "working", "agent": "codex"},
        },
        p2w,
    ) == MuxEvent("agent_status", "w2:t1", "w2:p1", AgentStatus("working", "codex", ""))
    assert translate_event(
        {"event": "tab_closed", "data": {"tab_id": "w2:t1"}}, p2w
    ) == MuxEvent("window_died", "w2:t1")
    assert (
        translate_event({"event": "pane_exited", "data": {"pane_id": "w2:p1"}}, p2w)
        is None
    )
    assert (
        translate_event({"event": "tab_closed", "data": {"tab_id": "w9:t9"}}, p2w)
        is None
    )
    assert translate_event({"event": "pane_focused", "data": {}}, p2w) is None


def _stream_of(*batches: list[dict]):
    seen: list[list] = []

    async def opener(subscriptions):
        index = len(seen)
        seen.append(list(subscriptions))
        yield SUBSCRIBED
        for event in batches[index] if index < len(batches) else []:
            yield event

    opener.seen = seen  # type: ignore[attr-defined]
    return opener


async def test_watch_events_reprimes_filters_and_streams() -> None:
    opener = _stream_of(
        [
            {
                "event": "pane.agent_status_changed",
                "data": {
                    "pane_id": "w2:p1",
                    "agent_status": "working",
                    "agent": "codex",
                },
            },
            {"event": "pane.agent_status_changed", "data": {"pane_id": "w9:p9"}},
            {"event": "tab_closed", "data": {"tab_id": "w2:t1"}},
        ]
    )
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "get", out=PANE_GET)
    )
    mgr = HerdrManager(socket_path="/tmp/s.sock", runner=fake, stream_opener=opener)

    got: list[MuxEvent] = []
    async for event in mgr.watch_events(["w2:t1"]):
        got.append(event)
        if len(got) >= 3:
            break

    assert got == [
        MuxEvent("agent_status", "w2:t1", "w2:p1", AgentStatus("idle", "claude", "")),
        MuxEvent("agent_status", "w2:t1", "w2:p1", AgentStatus("working", "codex", "")),
        MuxEvent("window_died", "w2:t1"),
    ]
    subs = opener.seen[0]  # type: ignore[attr-defined]
    assert {"type": "tab.closed"} in subs
    assert {"type": "pane.agent_status_changed", "pane_id": "w2:p1"} in subs
    assert {"type": "pane.exited"} not in subs


async def test_watch_events_reconnects_after_stream_drops(monkeypatch) -> None:
    monkeypatch.setattr("ccgram.multiplexer.herdr._STREAM_BACKOFF_BASE", 0.0)
    opener = _stream_of(
        [{"event": "tab_closed", "data": {"tab_id": "w2:t1"}}],
        [
            {
                "event": "pane.agent_status_changed",
                "data": {"pane_id": "w2:p1", "agent_status": "done"},
            }
        ],
    )
    fake = (
        FakeHerdr()
        .on("pane", "list", out=PANE_LIST_SINGLE)
        .on("pane", "get", out=PANE_GET)
    )
    mgr = HerdrManager(socket_path="/tmp/s.sock", runner=fake, stream_opener=opener)

    got: list[MuxEvent] = []
    async for event in mgr.watch_events(["w2:t1"]):
        got.append(event)
        if len(got) >= 4:
            break

    kinds = [(e.kind, e.status.state if e.status else None) for e in got]
    assert kinds == [
        ("agent_status", "idle"),
        ("window_died", None),
        ("agent_status", "idle"),
        ("agent_status", "done"),
    ]
    assert len(opener.seen) >= 2  # type: ignore[attr-defined]
