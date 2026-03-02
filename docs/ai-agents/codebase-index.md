# Codebase Index

## Where to Look First

Entry points:

- `src/ccbot/main.py`: process bootstrap.
- `src/ccbot/cli.py`: CLI command surface and config override rules.
- `src/ccbot/bot.py`: handler registration and main user flows.

State and routing:

- `src/ccbot/session.py`: thread bindings, window states, read offsets.
- `src/ccbot/window_resolver.py`: stale window ID migration/re-resolution.
- `src/ccbot/state_persistence.py`: atomic/debounced JSON persistence.

Monitoring and parsing:

- `src/ccbot/session_monitor.py`: polling engine for sessions and hook events.
- `src/ccbot/transcript_parser.py`: Claude transcript parsing and tool pairing.
- `src/ccbot/terminal_parser.py`: terminal status/UI detection.

Telegram handler surface:

- `src/ccbot/handlers/text_handler.py`: text-path orchestrator.
- `src/ccbot/handlers/message_queue.py`: ordering, merge rules, status conversion.
- `src/ccbot/handlers/status_polling.py`: background status and stale-session cleanup.
- `src/ccbot/handlers/directory_browser.py` + `directory_callbacks.py`: new-session UX.
- `src/ccbot/handlers/interactive_ui.py` + `interactive_callbacks.py`: interactive prompt UX.
- `src/ccbot/handlers/sessions_dashboard.py`: `/sessions` dashboard behavior.

Provider and command surface:

- `src/ccbot/providers/`: provider contract and implementations.
- `src/ccbot/cc_commands.py`: command/skill discovery and registration.
- `src/ccbot/hook.py`: Claude hook install/status/uninstall and event writes.

## Decision Map (Where to Edit)

Change topic/window routing behavior:

- `src/ccbot/session.py` for bindings/state model.
- `src/ccbot/handlers/callback_helpers.py` for thread/window extraction helpers.
- `src/ccbot/window_resolver.py` for stale ID re-resolution.

Change monitor/event dispatch behavior:

- `src/ccbot/session_monitor.py` for polling and fan-out.
- `src/ccbot/monitor_state.py` for byte-offset persistence.
- `src/ccbot/handlers/hook_events.py` for hook event handling.

Change provider behavior (commands, parsing, capabilities):

- `src/ccbot/providers/base.py` for contract/capabilities.
- `src/ccbot/providers/__init__.py` for per-window provider resolution.
- `src/ccbot/providers/{claude,codex,gemini}.py` for provider-specific behavior.
- `src/ccbot/interactive_prompt_formatter.py` for provider-facing interactive prompt text normalization (currently Codex edit approval readability).

Change Telegram interactive UX:

- `src/ccbot/handlers/interactive_ui.py` and `interactive_callbacks.py`.
- `src/ccbot/handlers/callback_data.py` for callback key contracts.
- `src/ccbot/handlers/message_queue.py` for ordering/merge side effects.

Change tmux behavior:

- `src/ccbot/tmux_manager.py` only.

## Change Mapping by Task Type

Add or change a Telegram command:

- Start in `src/ccbot/bot.py` command wiring.
- Implement behavior in `src/ccbot/handlers/` module.
- Add callback constants in `handlers/callback_data.py` when needed.

Change session binding logic:

- `src/ccbot/session.py` and `src/ccbot/window_resolver.py`.
- Validate persistence compatibility in `tests/ccbot/test_state_migration.py`.

Adjust transcript/status parsing:

- provider-specific parsing in `src/ccbot/providers/*.py`.
- shared parse behavior in `transcript_parser.py` / `terminal_parser.py`.

Touch tmux behavior:

- `src/ccbot/tmux_manager.py` only; avoid shell calls spread across handlers.

## Contracts You Must Not Break

- Keep topic-window identity 1:1 and window-id keyed.
- Preserve tool-use/tool-result pairing and in-order delivery.
- Keep provider logic behind provider interfaces/capabilities.
- Keep parsing full-fidelity; split only in Telegram send path.
- Use `handlers/user_state.py` keys for `context.user_data`; avoid new raw string keys.

## Debug Index

Symptom: messages routed to wrong topic/window

- inspect `thread_bindings` and window IDs in `session.py`.
- confirm callback/thread ID extraction in `handlers/callback_helpers.py`.

Symptom: no new assistant messages

- inspect `session_monitor.py` byte offsets and session map updates.
- verify provider parser compatibility for that window/provider.

Symptom: interactive keyboard not shown

- inspect `handlers/interactive_ui.py` and provider `parse_terminal_status` output.
- check hook events path (`hook.py` -> `handlers/hook_events.py`) for Claude.

Symptom: duplicated or out-of-order status/content messages

- inspect merge/send behavior in `handlers/message_queue.py`.
