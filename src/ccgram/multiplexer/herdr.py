from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from pathlib import Path

import structlog

from .base import (
    AgentStatus,
    CaptureResult,
    ForegroundInfo,
    MultiplexerCapabilities,
    MuxEvent,
    PaneDims,
    PaneInfo,
    WindowRef,
    WorkspaceRef,
)
from .herdr_events import (
    is_subscribed_sentinel,
    open_socket_stream,
    translate_event,
)
from .topic_mapping import format_agent_topic_prefix

__all__ = [
    "HERDR_PROTOCOL_VERSION",
    "HERDR_SUPPORTED_PROTOCOLS",
    "HerdrError",
    "HerdrManager",
    "HerdrProtocolError",
]

logger = structlog.get_logger()

HERDR_SUPPORTED_PROTOCOLS = frozenset({14, 15, 16})
HERDR_PROTOCOL_VERSION = max(HERDR_SUPPORTED_PROTOCOLS)

_HERDR_CAPABILITIES = MultiplexerCapabilities(
    name="herdr",
    ids_stable_across_restart=False,
    exposes_pane_tty=False,
    native_agent_status=True,
    read_max_lines=1000,
    self_identify_env="HERDR_PANE_ID",
    supports_event_stream=True,
    native_worktrees=True,
)

_INTERNAL_LABEL_RE = re.compile(r"^__.*__$")

_KEY_ALIASES: Mapping[str, str] = {
    "BSpace": "Backspace",
    "Space": "space",
}

HerdrRunner = Callable[[Sequence[str]], "Awaitable[tuple[int, str, str]]"]

HerdrStreamOpener = Callable[[Sequence[Mapping[str, object]]], "AsyncIterator[dict]"]

_RC_TIMEOUT = 124
_RC_NO_BINARY = 127
_CALL_TIMEOUT_SECONDS = 8.0

_STREAM_BACKOFF_BASE = 1.0
_STREAM_BACKOFF_MAX = 30.0


class HerdrError(RuntimeError):
    pass


class HerdrProtocolError(HerdrError):
    pass


def _pane_index(pane_id: str) -> int:
    _, sep, num = pane_id.rpartition(":p")
    return int(num) if sep and num.isdigit() else 0


class HerdrManager:
    @property
    def capabilities(self) -> MultiplexerCapabilities:
        return _HERDR_CAPABILITIES

    def __init__(
        self,
        *,
        socket_path: str | None = None,
        binary: str = "herdr",
        runner: HerdrRunner | None = None,
        stream_opener: HerdrStreamOpener | None = None,
    ) -> None:
        self._socket_path = socket_path or os.environ.get("HERDR_SOCKET_PATH", "")
        self._binary = shutil.which(binary) or binary
        self._run: HerdrRunner = runner or self._subprocess_run
        self._open_stream: HerdrStreamOpener = stream_opener or self._default_stream
        self._last_window_refs: list[WindowRef] | None = None
        self.last_window_scan_failed = False

    def _default_stream(
        self, subscriptions: Sequence[Mapping[str, object]]
    ) -> AsyncIterator[dict]:
        return open_socket_stream(self._socket_path, subscriptions)

    @staticmethod
    async def _kill_process(proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(ProcessLookupError, TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=1.0)

    async def _subprocess_run(self, args: Sequence[str]) -> tuple[int, str, str]:
        env = dict(os.environ)
        if self._socket_path:
            env["HERDR_SOCKET_PATH"] = self._socket_path
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                close_fds=False,
            )
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=_CALL_TIMEOUT_SECONDS
            )
        except TimeoutError:
            if proc is not None:
                await self._kill_process(proc)
            return (_RC_TIMEOUT, "", "herdr call timed out")
        except asyncio.CancelledError:
            if proc is not None:
                await self._kill_process(proc)
            raise
        except OSError as exc:
            return (_RC_NO_BINARY, "", str(exc))

        assert proc is not None
        stdout = out if isinstance(out, str) else out.decode(errors="replace")
        stderr = err if isinstance(err, str) else err.decode(errors="replace")
        return (proc.returncode or 0, stdout, stderr)

    async def _call_json(self, args: Sequence[str]) -> dict | None:
        rc, out, err = await self._run(args)
        if rc != 0:
            logger.debug("herdr call failed", args=list(args), rc=rc, err=err.strip())
            return None
        try:
            payload = json.loads(out)
        except json.JSONDecodeError, ValueError:
            logger.debug("herdr returned non-JSON", args=list(args))
            return None
        if not isinstance(payload, dict):
            return None
        if "error" in payload:
            logger.debug("herdr error payload", args=list(args), error=payload["error"])
            return None
        result = payload.get("result")
        return result if isinstance(result, dict) else None

    async def _call_ok(self, args: Sequence[str]) -> bool:
        rc, out, err = await self._run(args)
        if rc != 0:
            logger.debug("herdr call failed", args=list(args), rc=rc, err=err.strip())
            return False
        text = out.strip()
        if not text:
            return True
        try:
            payload = json.loads(text)
        except json.JSONDecodeError, ValueError:
            return True
        return not (isinstance(payload, dict) and "error" in payload)

    async def _call_text(self, args: Sequence[str]) -> str | None:
        rc, out, err = await self._run(args)
        if rc != 0:
            logger.debug("herdr read failed", args=list(args), rc=rc, err=err.strip())
            return None
        text = out.rstrip()
        return text or None

    async def _pane_get(self, pane_id: str) -> dict | None:
        result = await self._call_json(["pane", "get", pane_id])
        if not result:
            return None
        pane = result.get("pane")
        return pane if isinstance(pane, dict) else None

    async def _panes_for_tab(self, tab_id: str) -> list[dict]:
        pane_result = await self._call_json(["pane", "list"])
        if not pane_result:
            return []
        return [p for p in pane_result.get("panes", []) if p.get("tab_id") == tab_id]

    async def _active_pane(self, tab_id: str) -> str | None:
        panes = await self._panes_for_tab(tab_id)
        if not panes:
            return None
        focused = next((p for p in panes if p.get("focused")), None)
        chosen = focused or panes[0]
        return chosen.get("pane_id") or None

    async def _pane_belongs_to_tab(self, pane_id: str, tab_id: str) -> bool:
        panes = await self._panes_for_tab(tab_id)
        return any(p.get("pane_id") == pane_id for p in panes)

    def _window_refs_after_failed_scan(self) -> list[WindowRef] | None:
        already_failed = self.last_window_scan_failed
        self.last_window_scan_failed = True
        if self._last_window_refs is not None:
            if not already_failed:
                logger.debug("herdr window scan failed; returning cached window refs")
            return list(self._last_window_refs)
        if not already_failed:
            logger.debug("herdr window scan failed with no cached window refs")
        return None

    async def _tab_list(self) -> list[dict] | None:
        result = await self._call_json(["tab", "list"])
        if result is None:
            return None
        return [t for t in result.get("tabs", []) if t.get("tab_id")]

    async def _tab_get(self, tab_id: str) -> dict | None:
        if not tab_id:
            return None
        result = await self._call_json(["tab", "get", tab_id])
        if not result:
            return None
        tab = result.get("tab")
        return tab if isinstance(tab, dict) else None

    async def _workspace_labels(self) -> dict[str, str] | None:
        result = await self._call_json(["workspace", "list"])
        if result is None:
            return None
        return {
            w.get("workspace_id", ""): w.get("label", "")
            for w in result.get("workspaces", [])
            if w.get("workspace_id")
        }

    async def _workspace_labels_for_window_scan(self) -> dict[str, str] | None:
        labels = await self._workspace_labels()
        if labels is not None:
            return labels
        if self._last_window_refs is not None:
            return None
        return {}

    @staticmethod
    def _to_window_ref(
        tab_id: str,
        window_name: str,
        cwd: str,
        agent: str,
    ) -> WindowRef:
        return WindowRef(
            window_id=tab_id,
            window_name=window_name or agent,
            cwd=cwd,
            pane_current_command=agent,
        )

    async def ensure_session(self) -> None:
        rc, out, err = await self._run(["status", "--json"])
        if rc != 0:
            raise HerdrError(f"herdr status failed: {err.strip() or f'exit {rc}'}")
        try:
            status = json.loads(out)
        except (json.JSONDecodeError, ValueError) as exc:
            raise HerdrError("herdr status returned non-JSON") from exc
        if not isinstance(status, dict):
            raise HerdrError("herdr status returned non-object JSON")
        server = status.get("server")
        if not isinstance(server, dict):
            raise HerdrError("herdr status returned invalid server object")
        if not server.get("running"):
            raise HerdrError("herdr server is not running")
        proto = server.get("protocol")
        cli_server_compatible = server.get("compatible")
        is_supported_protocol = isinstance(proto, int) and not isinstance(proto, bool)
        is_supported_protocol = (
            is_supported_protocol and proto in HERDR_SUPPORTED_PROTOCOLS
        )
        if not is_supported_protocol or cli_server_compatible is False:
            logger.warning(
                "herdr protocol is unverified; continuing",
                server_protocol=proto,
                supported_protocols=sorted(HERDR_SUPPORTED_PROTOCOLS),
                cli_server_compatible=cli_server_compatible,
            )

    @staticmethod
    def _representative_pane(tab_panes: list[dict], tab_cwd: str) -> tuple[str, str]:
        focused = next((p for p in tab_panes if p.get("focused")), None)
        if focused:
            agent = focused.get("display_agent") or focused.get("agent", "")
            cwd = focused.get("cwd", "") or tab_cwd
            if agent:
                return agent, cwd
        for pane in tab_panes:
            candidate = pane.get("display_agent") or pane.get("agent", "")
            if candidate:
                return candidate, pane.get("cwd", "") or tab_cwd
        cwd = (focused or {}).get("cwd", "") or tab_cwd if focused else tab_cwd
        return "", cwd

    async def list_windows(self) -> list[WindowRef]:
        return await self.list_windows_for_reconciliation() or []

    async def list_windows_for_reconciliation(self) -> list[WindowRef] | None:
        tabs = await self._tab_list()
        if tabs is None:
            return self._window_refs_after_failed_scan()
        if not tabs:
            self.last_window_scan_failed = False
            self._last_window_refs = []
            return []
        workspace_labels = await self._workspace_labels_for_window_scan()
        if workspace_labels is None:
            return self._window_refs_after_failed_scan()

        pane_result = await self._call_json(["pane", "list"])
        if pane_result is None:
            return self._window_refs_after_failed_scan()
        self.last_window_scan_failed = False
        panes_by_tab: dict[str, list[dict]] = {}
        if pane_result:
            for pane in pane_result.get("panes", []):
                tid = pane.get("tab_id", "")
                if tid:
                    panes_by_tab.setdefault(tid, []).append(pane)

        refs: list[WindowRef] = []
        for tab in tabs:
            tab_id = tab.get("tab_id", "")
            tab_label = tab.get("label", "")
            workspace_label = workspace_labels.get(tab.get("workspace_id", ""), "")

            if _INTERNAL_LABEL_RE.match(workspace_label) or _INTERNAL_LABEL_RE.match(
                tab_label
            ):
                continue

            tab_panes = panes_by_tab.get(tab_id, [])
            rep_agent, rep_cwd = self._representative_pane(
                tab_panes, tab.get("cwd", "")
            )
            window_name = format_agent_topic_prefix(workspace_label, tab_label)
            refs.append(self._to_window_ref(tab_id, window_name, rep_cwd, rep_agent))
        self._last_window_refs = list(refs)
        return refs

    async def find_window_by_id(self, window_id: str) -> WindowRef | None:
        tab = await self._tab_get(window_id)
        if tab is None:
            return None
        tab_label = tab.get("label", "")

        workspace_labels = await self._workspace_labels() or {}
        workspace_label = workspace_labels.get(tab.get("workspace_id", ""), "")
        window_name = format_agent_topic_prefix(workspace_label, tab_label)

        pane_result = await self._call_json(["pane", "list"])
        rep_agent = ""
        rep_cwd = tab.get("cwd", "")
        if pane_result:
            tab_panes = [
                p for p in pane_result.get("panes", []) if p.get("tab_id") == window_id
            ]
            focused = next((p for p in tab_panes if p.get("focused")), None)
            rep_pane = focused or (tab_panes[0] if tab_panes else None)
            if rep_pane:
                rep_agent = rep_pane.get("display_agent") or rep_pane.get("agent", "")
                rep_cwd = rep_pane.get("cwd", "") or rep_cwd

        return self._to_window_ref(window_id, window_name, rep_cwd, rep_agent)

    async def _read_visible_pane(
        self, pane_id: str, *, ansi: bool = False
    ) -> str | None:
        fmt = "ansi" if ansi else "text"
        return await self._call_text(
            ["pane", "read", pane_id, "--source", "visible", "--format", fmt]
        )

    async def _read_recent_pane(self, pane_id: str, *, lines: int) -> str | None:
        return await self._call_text(
            [
                "pane",
                "read",
                pane_id,
                "--source",
                "recent",
                "--lines",
                str(lines),
                "--format",
                "text",
            ]
        )

    async def _dims_for_pane(self, pane_id: str) -> PaneDims | None:
        result = await self._call_json(["pane", "layout", "--pane", pane_id])
        if not result:
            return None
        layout = result.get("layout") or {}
        for pane in layout.get("panes", []):
            if pane.get("pane_id") == pane_id:
                rect = pane.get("rect") or {}
                w, h = rect.get("width"), rect.get("height")
                if isinstance(w, int) and isinstance(h, int):
                    return PaneDims(width=w, height=h)
        area = layout.get("area") or {}
        w, h = area.get("width"), area.get("height")
        if isinstance(w, int) and isinstance(h, int):
            return PaneDims(width=w, height=h)
        return None

    async def _foreground_for_pane(self, pane_id: str) -> ForegroundInfo | None:
        result = await self._call_json(["pane", "process-info", "--pane", pane_id])
        if not result:
            return None
        info = result.get("process_info") or {}
        procs = info.get("foreground_processes") or []
        if not procs:
            return None
        pgid = info.get("foreground_process_group_id") or 0
        leader = next((p for p in procs if p.get("pid") == pgid), procs[0])
        return ForegroundInfo(
            pid=int(leader.get("pid", 0)),
            pgid=int(pgid or leader.get("pid", 0)),
            argv=list(leader.get("argv") or []),
            cwd=leader.get("cwd", "") or "",
            tty="",
        )

    async def capture_pane(self, window_id: str, with_ansi: bool = False) -> str | None:
        pane_id = await self._active_pane(window_id)
        if pane_id is None:
            return None
        return await self._read_visible_pane(pane_id, ansi=with_ansi)

    async def capture_scrollback(
        self, window_id: str, lines: int = 200
    ) -> CaptureResult | None:
        pane_id = await self._active_pane(window_id)
        if pane_id is None:
            return None
        max_lines = self.capabilities.read_max_lines
        effective = lines
        truncated = False
        if max_lines is not None and lines > max_lines:
            effective = max_lines
            truncated = True
        text = await self._read_recent_pane(pane_id, lines=effective)
        if text is None:
            return None
        return CaptureResult(text=text, truncated=truncated)

    async def pane_dims(self, window_id: str) -> PaneDims | None:
        pane_id = await self._active_pane(window_id)
        if pane_id is None:
            return None
        return await self._dims_for_pane(pane_id)

    async def send(
        self,
        window_id: str,
        text: str,
        *,
        enter: bool = True,
        literal: bool = True,
        raw: bool = False,  # noqa: ARG002
    ) -> bool:
        pane_id = await self._active_pane(window_id)
        if pane_id is None:
            return False
        return await self._send_to(pane_id, text, enter=enter, literal=literal)

    async def send_to_pane(
        self,
        pane_id: str,
        text: str,
        *,
        enter: bool = True,
        literal: bool = True,
        window_id: str | None = None,
    ) -> bool:
        if window_id is not None and not await self._pane_belongs_to_tab(
            pane_id, window_id
        ):
            return False
        return await self._send_to(pane_id, text, enter=enter, literal=literal)

    async def _send_to(
        self, pane_id: str, text: str, *, enter: bool, literal: bool
    ) -> bool:
        if not literal:
            keys = [_KEY_ALIASES.get(tok, tok) for tok in text.split() if tok]
            if enter:
                keys.append("Enter")
            if not keys:
                return False
            return await self._call_ok(["pane", "send-keys", pane_id, *keys])
        if enter:
            return await self._call_ok(["pane", "run", pane_id, text])
        return await self._call_ok(["pane", "send-text", pane_id, text])

    async def kill_window(self, window_id: str) -> bool:
        ok = await self._call_ok(["tab", "close", window_id])
        if ok:
            logger.info("Closed herdr tab %s", window_id)
        return ok

    async def rename_window(self, window_id: str, new_name: str) -> bool:
        return await self._call_ok(["tab", "rename", window_id, new_name])

    async def list_panes(self, window_id: str) -> list[PaneInfo]:
        panes = await self._panes_for_tab(window_id)
        if not panes:
            return []
        active_pane_id = next(
            (p.get("pane_id", "") for p in panes if p.get("focused")),
            panes[0].get("pane_id", ""),
        )
        layout_result = await self._call_json(
            ["pane", "layout", "--pane", active_pane_id]
        )
        layout_rects: dict[str, PaneDims] = {}
        if layout_result:
            layout = layout_result.get("layout") or {}
            area = layout.get("area") or {}
            area_w, area_h = area.get("width", 0), area.get("height", 0)
            for lp in layout.get("panes", []):
                pid = lp.get("pane_id", "")
                if pid:
                    rect = lp.get("rect") or {}
                    lw = rect.get("width")
                    lh = rect.get("height")
                    if isinstance(lw, int) and isinstance(lh, int):
                        layout_rects[pid] = PaneDims(width=lw, height=lh)
                    elif isinstance(area_w, int) and isinstance(area_h, int):
                        layout_rects[pid] = PaneDims(width=area_w, height=area_h)
        result: list[PaneInfo] = []
        for pane in panes:
            pid = pane.get("pane_id", "")
            dims = layout_rects.get(pid)
            result.append(
                PaneInfo(
                    pane_id=pid,
                    index=_pane_index(pid),
                    active=bool(pane.get("focused", False)),
                    command=pane.get("agent", ""),
                    path=pane.get("cwd", ""),
                    width=dims.width if dims else 0,
                    height=dims.height if dims else 0,
                )
            )
        return result

    async def list_workspaces(self) -> list[WorkspaceRef]:
        result = await self._call_json(["workspace", "list"])
        if not result:
            return []
        return [
            WorkspaceRef(
                workspace_id=ws.get("workspace_id", ""),
                label=ws.get("label", ""),
                cwd=ws.get("cwd", ""),
            )
            for ws in result.get("workspaces", [])
            if ws.get("workspace_id")
        ]

    async def _resolve_workspace_id(self, cwd: str) -> str:
        result = await self._call_json(["workspace", "list"])
        if result:
            for ws in result.get("workspaces", []):
                if self._same_path(ws.get("cwd", ""), cwd):
                    wid = ws.get("workspace_id", "")
                    if wid:
                        return wid
        created = await self._call_json(["workspace", "create", "--cwd", cwd])
        if not created:
            return ""
        return (created.get("workspace") or {}).get("workspace_id", "") or ""

    @staticmethod
    def _same_path(a: str, b: str) -> bool:
        if not a or not b:
            return False
        try:
            return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
        except OSError:
            return a == b

    async def _launch_agent_in_pane(
        self,
        *,
        tab_id: str,
        label: str,
        pane_id: str,
        launch_command: str,
        agent_args: str,
    ) -> tuple[bool, str]:
        if not pane_id:
            await self.kill_window(tab_id)
            return False, "herdr tab created without a root pane"
        cmd = f"{launch_command} {agent_args}".strip() if agent_args else launch_command
        if not await self._call_ok(["pane", "run", pane_id, cmd]):
            await self.kill_window(tab_id)
            return False, f"Failed to launch agent in herdr tab '{label}'"
        return True, ""

    async def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        start_agent: bool = True,
        agent_args: str = "",
        launch_command: str | None = None,
        *,
        workspace_id: str | None = None,
    ) -> tuple[bool, str, str, str]:
        path = Path(work_dir).expanduser()
        if not path.exists():
            return False, f"Directory does not exist: {work_dir}", "", ""
        if not path.is_dir():
            return False, f"Not a directory: {work_dir}", "", ""

        cwd = str(path)
        if workspace_id is None:
            workspace_id = await self._resolve_workspace_id(cwd)
        args = ["tab", "create", "--cwd", cwd, "--no-focus"]
        if workspace_id:
            args += ["--workspace", workspace_id]
        if window_name:
            args += ["--label", window_name]
        result = await self._call_json(args)
        if not result:
            return False, f"Failed to create herdr tab at {path}", "", ""

        tab = result.get("tab") or {}
        tab_id = tab.get("tab_id", "")
        label = tab.get("label", window_name or "")
        if not tab_id:
            return False, "herdr tab created without a tab id", "", ""

        if start_agent and launch_command:
            root_pane = result.get("root_pane") or {}
            launch_ok, launch_error = await self._launch_agent_in_pane(
                tab_id=tab_id,
                label=label,
                pane_id=root_pane.get("pane_id", ""),
                launch_command=launch_command,
                agent_args=agent_args,
            )
            if not launch_ok:
                return False, launch_error, "", ""

        logger.info("Created herdr tab %r (id=%s) at %s", label, tab_id, path)
        return True, f"Created herdr tab '{label}' at {path}", label, tab_id

    async def create_worktree_window(
        self,
        repo_path: str,
        worktree_path: str,
        branch: str,
        *,
        window_name: str | None = None,
        launch_command: str | None = None,
    ) -> tuple[bool, str, str, str]:
        repo = Path(repo_path).expanduser()
        if not repo.is_dir():
            return False, f"Repo path is not a directory: {repo_path}", "", ""

        args = [
            "worktree",
            "create",
            "--cwd",
            str(repo),
            "--branch",
            branch,
            "--path",
            worktree_path,
            "--no-focus",
            "--json",
        ]
        if window_name:
            args += ["--label", window_name]
        result = await self._call_json(args)
        if not result:
            return False, f"Failed to create herdr worktree at {worktree_path}", "", ""

        tab = result.get("tab") or {}
        root_pane = result.get("root_pane") or {}
        tab_id = tab.get("tab_id") or root_pane.get("tab_id", "")
        if not tab_id:
            tab_id = (result.get("workspace") or {}).get("active_tab_id", "")
        if not tab_id:
            return False, "herdr worktree created without a tab id", "", ""
        label = tab.get("label", window_name or "")

        if launch_command:
            pane_id = root_pane.get("pane_id") or await self._active_pane(tab_id)
            if pane_id:
                await self._call_ok(["pane", "run", pane_id, launch_command])

        logger.info(
            "Created herdr worktree window %r (id=%s) at %s",
            label,
            tab_id,
            worktree_path,
        )
        return (
            True,
            f"Created herdr worktree '{branch}' at {worktree_path}",
            label,
            tab_id,
        )

    async def stamp_pane_title(self, window_id: str, provider_name: str) -> None:
        pane_id = await self._active_pane(window_id)
        if pane_id is None:
            return
        await self._call_ok(
            [
                "pane",
                "report-metadata",
                pane_id,
                "--source",
                "ccgram",
                "--title",
                f"ccgram:{provider_name}",
            ]
        )

    async def foreground(self, window_id: str) -> ForegroundInfo | None:
        pane_id = await self._active_pane(window_id)
        if pane_id is None:
            return None
        return await self._foreground_for_pane(pane_id)

    async def agent_status(self, window_id: str) -> AgentStatus | None:
        pane_id = await self._active_pane(window_id)
        if pane_id is None:
            return None
        pane = await self._pane_get(pane_id)
        if pane is None:
            return None
        state = (pane.get("agent_status") or "").strip()
        if not state:
            return None
        return AgentStatus(
            state=state,
            agent=(pane.get("agent") or "").strip(),
            custom_status=(pane.get("custom_status") or "").strip(),
        )

    async def split_window(self, window_id: str) -> str | None:
        pane_id = await self._active_pane(window_id)
        if pane_id is None:
            return None
        result = await self._call_json(
            ["pane", "split", pane_id, "--direction", "down", "--no-focus"]
        )
        if not result:
            return None
        pane = result.get("pane")
        if not isinstance(pane, dict):
            return None
        new_id = pane.get("pane_id")
        return new_id if isinstance(new_id, str) and new_id else None

    async def _resolve_panes(self, window_ids: Sequence[str]) -> dict[str, str]:
        pane_to_window: dict[str, str] = {}
        for window_id in window_ids:
            pane_id = await self._active_pane(window_id)
            if pane_id:
                pane_to_window[pane_id] = window_id
        return pane_to_window

    async def watch_events(
        self, window_ids: Sequence[str]
    ) -> AsyncGenerator[MuxEvent, None]:
        ids = list(window_ids)
        backoff = _STREAM_BACKOFF_BASE
        while True:
            pane_to_window = await self._resolve_panes(ids)
            subscriptions: list[Mapping[str, object]] = [
                {"type": "tab.closed"},
                *(
                    {"type": "pane.agent_status_changed", "pane_id": pane}
                    for pane in pane_to_window
                ),
            ]
            try:
                async for obj in self._open_stream(subscriptions):
                    if is_subscribed_sentinel(obj):
                        backoff = _STREAM_BACKOFF_BASE
                        for pane_id, window_id in pane_to_window.items():
                            status = await self.agent_status(window_id)
                            if status is not None:
                                yield MuxEvent(
                                    kind="agent_status",
                                    window_id=window_id,
                                    pane_id=pane_id,
                                    status=status,
                                )
                        continue
                    event = translate_event(obj, pane_to_window)
                    if event is not None:
                        yield event
            except OSError as exc:
                logger.debug("herdr event stream error: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _STREAM_BACKOFF_MAX)

    async def capture_pane_by_id(
        self,
        pane_id: str,
        *,
        with_ansi: bool = False,
        window_id: str | None = None,
    ) -> str | None:
        if window_id is not None and not await self._pane_belongs_to_tab(
            pane_id, window_id
        ):
            return None
        return await self._read_visible_pane(pane_id, ansi=with_ansi)

    async def capture_pane_scrollback(
        self, window_id: str, history: int = 200
    ) -> str | None:
        result = await self.capture_scrollback(window_id, lines=history)
        return result.text if result else None

    async def send_keys(
        self,
        window_id: str,
        text: str,
        enter: bool = True,
        literal: bool = True,
        *,
        raw: bool = False,
    ) -> bool:
        return await self.send(window_id, text, enter=enter, literal=literal, raw=raw)

    async def send_keys_to_pane(
        self,
        pane_id: str,
        text: str,
        *,
        enter: bool = True,
        literal: bool = True,
        window_id: str | None = None,
    ) -> bool:
        return await self.send_to_pane(
            pane_id, text, enter=enter, literal=literal, window_id=window_id
        )

    async def get_pane_title(self, window_id: str) -> str:
        pane_id = await self._active_pane(window_id)
        if pane_id is None:
            return ""
        pane = await self._pane_get(pane_id)
        if pane is None:
            return ""
        return pane.get("title", "") or ""
