import re
import structlog
from dataclasses import dataclass
from typing import Any, Protocol

from ...telegram_client import TelegramClient

from ...providers.shell import match_prompt
from ...thread_router import thread_router
from ...multiplexer import multiplexer as tmux_manager
from ...multiplexer.base import CaptureResult
from ..messaging_pipeline.message_sender import (
    REACT_DONE,
    REACT_FAIL,
    edit_with_fallback,
    rate_limit_send_message,
    react,
)
from ...topic_state_registry import topic_state

logger = structlog.get_logger()


async def _react_exit(
    client: TelegramClient, chat_id: int, message_id: int, exit_code: int
) -> None:
    if not message_id:
        return
    emoji = REACT_DONE if exit_code == 0 else REACT_FAIL
    await react(client, chat_id, message_id, emoji)


class CommandApprovalCallback(Protocol):
    async def __call__(
        self,
        client: TelegramClient,
        chat_id: int,
        thread_id: int,
        window_id: str,
        result: Any,
        user_id: int,
    ) -> bool: ...


async def _approval_unwired(  # type: ignore[misc]
    _bot: Any,
    _chat_id: Any,
    _thread_id: Any,
    _window_id: Any,
    _result: Any,
    _user_id: Any,
) -> bool:
    raise RuntimeError(
        "register_approval_callback not wired — call register_approval_callback() at startup"
    )


_approval_callback: CommandApprovalCallback = _approval_unwired  # type: ignore[assignment]
_approval_callback_registered: bool = False


def register_approval_callback(fn: CommandApprovalCallback) -> None:
    global _approval_callback, _approval_callback_registered
    if _approval_callback_registered:
        raise RuntimeError("register_approval_callback already registered")
    _approval_callback = fn
    _approval_callback_registered = True


def _reset_approval_callback_for_testing() -> None:
    global _approval_callback, _approval_callback_registered
    _approval_callback = _approval_unwired  # type: ignore[assignment]
    _approval_callback_registered = False


_OUTPUT_LIMIT = 3800

_MAX_FIX_OUTPUT_CHARS = 800

_GLYPH_RE = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd]")

_SCROLLBACK_LINES = 200

_TRUNCATION_NOTICE = "… [earlier output truncated]"


def _passive_scrollback_lines() -> int:
    cap = tmux_manager.capabilities.read_max_lines
    return cap if cap is not None else _SCROLLBACK_LINES


async def _capture_with_scrollback(
    window_id: str, history: int = _SCROLLBACK_LINES
) -> CaptureResult | None:
    return await tmux_manager.capture_scrollback(window_id, lines=history)


@dataclass
class _CommandOutput:
    text: str
    exit_code: int | None = None


@dataclass
class _PassiveOutput:
    command_echo: str
    echo_index: int
    text: str
    exit_code: int | None = None


@dataclass
class _ShellMonitorState:
    last_text_hash: int = 0
    last_command_echo: str = ""
    last_echo_index: int = -1
    msg_id: int | None = None
    last_output: str = ""
    exit_code_sent: bool = False
    tracked_done: bool = False
    telegram_command: str = ""
    telegram_user_id: int = 0
    telegram_thread_id: int = 0
    telegram_message_id: int = 0
    telegram_generation: int = 0


_shell_monitor_state: dict[str, _ShellMonitorState] = {}
_fix_generation: int = 0


def strip_terminal_glyphs(text: str) -> str:
    return _GLYPH_RE.sub("", text)


def _extract_command_output(current: str) -> _CommandOutput:
    lines = current.rstrip().splitlines()
    if not lines:
        return _CommandOutput(text="")

    scan_start = max(0, len(lines) - 10)
    end_idx = None
    exit_code = None
    for i in range(len(lines) - 1, scan_start - 1, -1):
        m = match_prompt(lines[i])
        if m and not m.trailing_text.strip():
            end_idx = i
            exit_code = m.exit_code
            break

    if end_idx is None:
        return _CommandOutput(text="")

    start_idx = None
    for i in range(end_idx - 1, -1, -1):
        m = match_prompt(lines[i])
        if m and m.trailing_text.strip():
            start_idx = i
            break

    if start_idx is None:
        return _CommandOutput(text="", exit_code=exit_code)

    output_lines = lines[start_idx + 1 : end_idx]
    return _CommandOutput(text="\n".join(output_lines), exit_code=exit_code)


def _find_command_echo(lines: list[str]) -> tuple[str, int] | None:
    scan_start = max(0, len(lines) - 10)
    for i in range(len(lines) - 1, scan_start - 1, -1):
        m = match_prompt(lines[i])
        if m and not m.trailing_text.strip():
            for j in range(i - 1, -1, -1):
                mj = match_prompt(lines[j])
                if mj and mj.trailing_text.strip():
                    return (lines[j], j)
            return None
    return None


def _find_in_progress(lines: list[str]) -> _PassiveOutput | None:
    for i in range(len(lines) - 1, -1, -1):
        m = match_prompt(lines[i])
        if m and m.trailing_text.strip():
            output_lines = lines[i + 1 :]
            while output_lines and not output_lines[-1].strip():
                output_lines.pop()
            return _PassiveOutput(
                command_echo=lines[i],
                echo_index=i,
                text="\n".join(output_lines),
            )
    return None


def _extract_passive_output(text: str) -> _PassiveOutput | None:
    lines = text.rstrip().splitlines()
    if not lines:
        return None

    tail = lines[max(0, len(lines) - 10) :]
    if not any(match_prompt(line) for line in tail):
        return None

    result = _extract_command_output(text)
    if result.exit_code is not None:
        found = _find_command_echo(lines)
        if found is None:
            return None
        echo_text, echo_idx = found
        return _PassiveOutput(
            command_echo=echo_text,
            echo_index=echo_idx,
            text=result.text,
            exit_code=result.exit_code,
        )

    return _find_in_progress(lines)


def _truncated_tail_output(text: str) -> _PassiveOutput | None:
    lines = text.rstrip().splitlines()
    tail_start = max(0, len(lines) - 10)
    end_idx = None
    exit_code = None
    for i in range(len(lines) - 1, tail_start - 1, -1):
        m = match_prompt(lines[i])
        if m and not m.trailing_text.strip():
            end_idx = i
            exit_code = m.exit_code
            break
    if end_idx is None:
        return None

    output_lines = lines[:end_idx]
    while output_lines:
        m = match_prompt(output_lines[0])
        if m and not m.trailing_text.strip():
            output_lines.pop(0)
        else:
            break
    tail = "\n".join(output_lines).rstrip()
    if not tail.strip():
        return None
    return _PassiveOutput(
        command_echo="",
        echo_index=-1,
        text=tail,
        exit_code=exit_code,
    )


def _extract_passive_with_truncation(
    capture: CaptureResult, requested: int, *, allow_echoless_recovery: bool
) -> tuple[_PassiveOutput, bool] | None:
    passive = _extract_passive_output(capture.text)
    if passive is not None:
        return passive, capture.truncated

    if not allow_echoless_recovery:
        return None

    cap = tmux_manager.capabilities.read_max_lines
    if cap is not None and requested >= cap:
        tail = _truncated_tail_output(capture.text)
        if tail is not None:
            return tail, True
    return None


def _apply_echoless_identity(
    state: _ShellMonitorState, passive: _PassiveOutput
) -> None:
    if passive.echo_index != -1 or passive.command_echo:
        return
    if not state.tracked_done and (state.last_command_echo or state.msg_id is not None):
        passive.command_echo = state.last_command_echo
        passive.echo_index = state.last_echo_index
    else:
        passive.echo_index = state.telegram_generation


def mark_telegram_command(
    window_id: str,
    command: str,
    user_id: int,
    thread_id: int,
    message_id: int = 0,
) -> None:
    global _fix_generation  # noqa: PLW0603
    _fix_generation += 1
    state = _shell_monitor_state.setdefault(window_id, _ShellMonitorState())
    state.telegram_command = command
    state.telegram_user_id = user_id
    state.telegram_thread_id = thread_id
    state.telegram_message_id = message_id
    state.telegram_generation = _fix_generation


async def _relay_output(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    output: str,
    *,
    msg_id: int | None = None,
) -> int | None:
    display = strip_terminal_glyphs(output)
    if len(display) > _OUTPUT_LIMIT:
        display = "\u2026 " + display[-_OUTPUT_LIMIT:]

    if not display.strip():
        return msg_id

    display = display.replace("```", "` ` `")
    formatted = f"```\n{display}\n```"

    if msg_id is None:
        sent = await rate_limit_send_message(
            client,
            chat_id,
            formatted,
            message_thread_id=thread_id,
        )
        if sent:
            return sent.message_id
        return None
    else:
        await edit_with_fallback(client, chat_id, msg_id, formatted)
        return msg_id


async def _update_error_message(
    client: TelegramClient, chat_id: int, msg_id: int, exit_code: int, output: str
) -> None:
    error_prefix = f"\u274c exit {exit_code}\n"
    display = strip_terminal_glyphs(output)
    fence_overhead = 8
    max_body = _OUTPUT_LIMIT - len(error_prefix) - fence_overhead
    if len(display) > max_body:
        display = display[-max_body:]
    display = display.replace("```", "` ` `")
    formatted = f"{error_prefix}```\n{display}\n```"
    await edit_with_fallback(client, chat_id, msg_id, formatted)


async def _maybe_suggest_fix(
    client: TelegramClient,
    user_id: int,
    chat_id: int,
    thread_id: int,
    window_id: str,
    *,
    command: str,
    exit_code: int,
    msg_id: int | None,
    output: str,
    generation: int = 0,
) -> None:
    if msg_id:
        await _update_error_message(client, chat_id, msg_id, exit_code, output)

    try:
        from ...llm import get_completer

        completer = get_completer()
    except ValueError, ImportError:
        completer = None

    if not completer:
        return

    from .shell_context import gather_llm_context, redact_for_llm

    ctx = await gather_llm_context(window_id)
    trimmed = redact_for_llm(output or "")
    if len(trimmed) > _MAX_FIX_OUTPUT_CHARS:
        trimmed = f"\u2026{trimmed[-_MAX_FIX_OUTPUT_CHARS:]}"

    fix_description = (
        f"The command `{command}` failed (exit {exit_code}):\n{trimmed}\n\n"
        "Generate a corrected command."
    )

    try:
        result = await completer.generate_command(
            fix_description,
            cwd=ctx["cwd"],
            shell=ctx["shell"],
            shell_tools=ctx["shell_tools"],
        )
    except RuntimeError:
        logger.debug("LLM fix suggestion failed")
        return

    if generation and _fix_generation != generation:
        return

    if not result.command or result.command == command:
        return

    await _approval_callback(client, chat_id, thread_id, window_id, result, user_id)


@topic_state.register("window")
def clear_shell_monitor_state(window_id: str) -> None:
    _shell_monitor_state.pop(window_id, None)


def reset_shell_monitor_state() -> None:
    _shell_monitor_state.clear()


def _reset_monitor(state: _ShellMonitorState) -> None:
    state.last_command_echo = ""
    state.last_echo_index = -1
    state.msg_id = None
    state.last_output = ""
    state.exit_code_sent = False
    state.tracked_done = False


def _has_markers_in_tail(rendered_text: str) -> bool:
    lines = rendered_text.rstrip().splitlines()
    tail = lines[max(0, len(lines) - 10) :]
    return any(match_prompt(line.lstrip()) for line in tail)


async def check_passive_shell_output(
    client: TelegramClient,
    user_id: int,
    thread_id: int,
    window_id: str,
    rendered_text: str,
) -> None:
    text_hash = hash(rendered_text)
    state = _shell_monitor_state.setdefault(window_id, _ShellMonitorState())
    changed = text_hash != state.last_text_hash
    if not changed:
        return
    state.last_text_hash = text_hash

    if not _has_markers_in_tail(rendered_text):
        if not (state.last_command_echo and state.msg_id is not None):
            _reset_monitor(state)
        return

    requested = _passive_scrollback_lines()
    capture = await _capture_with_scrollback(window_id, history=requested)
    if capture is None or not capture.text:
        return

    command_in_flight = bool(
        state.last_command_echo or state.msg_id is not None or state.telegram_command
    )
    extracted = _extract_passive_with_truncation(
        capture, requested, allow_echoless_recovery=command_in_flight
    )
    if extracted is None:
        if not (state.last_command_echo and state.msg_id is not None):
            _reset_monitor(state)
        return
    passive, truncated = extracted
    _apply_echoless_identity(state, passive)

    if truncated and passive.text:
        passive.text = f"{_TRUNCATION_NOTICE}\n{passive.text}"

    if (
        passive.command_echo != state.last_command_echo
        or passive.echo_index != state.last_echo_index
    ):
        state.last_command_echo = passive.command_echo
        state.last_echo_index = passive.echo_index
        state.msg_id = None
        state.last_output = ""
        state.exit_code_sent = False
        state.tracked_done = False

    await _relay_passive_output(client, user_id, thread_id, state, passive, window_id)
    if passive.exit_code is not None:
        state.tracked_done = True


def _command_from_echo(echo: str) -> str:
    m = match_prompt(echo)
    return m.trailing_text.strip() if m else echo


async def _relay_passive_output(
    client: TelegramClient,
    user_id: int,
    thread_id: int,
    state: _ShellMonitorState,
    passive: _PassiveOutput,
    window_id: str,
) -> None:
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)

    if passive.text != state.last_output:
        state.last_output = passive.text
        cmd = _command_from_echo(passive.command_echo)
        combined = f"❯ {cmd}\n{passive.text}" if cmd else passive.text
        state.msg_id = await _relay_output(
            client, chat_id, thread_id, combined, msg_id=state.msg_id
        )

    if (
        passive.exit_code is not None
        and passive.exit_code != 0
        and not state.exit_code_sent
        and state.msg_id
    ):
        state.exit_code_sent = True
        await _update_error_message(
            client, chat_id, state.msg_id, passive.exit_code, passive.text
        )

    if passive.exit_code is not None and state.telegram_message_id:
        await _react_exit(client, chat_id, state.telegram_message_id, passive.exit_code)
        if passive.exit_code == 0:
            state.telegram_message_id = 0

    if (
        passive.exit_code is not None
        and passive.exit_code != 0
        and state.telegram_command
    ):
        tg_cmd = state.telegram_command
        tg_uid = state.telegram_user_id
        tg_tid = state.telegram_thread_id
        tg_gen = state.telegram_generation
        state.telegram_command = ""
        state.telegram_user_id = 0
        state.telegram_thread_id = 0
        state.telegram_message_id = 0
        state.telegram_generation = 0
        await _maybe_suggest_fix(
            client,
            tg_uid,
            chat_id,
            tg_tid,
            window_id,
            command=tg_cmd,
            exit_code=passive.exit_code,
            msg_id=state.msg_id,
            output=passive.text,
            generation=tg_gen,
        )
