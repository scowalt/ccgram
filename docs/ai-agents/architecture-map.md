# Architecture Map

## Runtime Layers

1. CLI and bootstrap

- `src/ccbot/main.py` starts logging and launches the PTB application.
- `src/ccbot/cli.py` maps CLI flags to env vars before config loads.

2. Bot orchestration

- `src/ccbot/bot.py` wires handlers and owns callback dispatch.
- Topic routing and authorization checks live here.

3. Session and monitor core

- `src/ccbot/session.py` is the state hub (thread bindings, window states, offsets).
- `src/ccbot/session_monitor.py` tails transcripts/events and emits parsed messages.
- `src/ccbot/monitor_state.py` persists byte offsets for incremental reads.

4. Provider abstraction

- `src/ccbot/providers/base.py` defines the provider contract.
- `src/ccbot/providers/__init__.py` resolves per-window provider selection.
- `src/ccbot/providers/{claude,codex,gemini}.py` implement provider-specific behavior.
- `src/ccbot/interactive_prompt_formatter.py` normalizes provider interactive prompt text for Telegram readability (currently Codex edit approvals).

5. Integrations

- `src/ccbot/tmux_manager.py` is the tmux IO boundary.
- `src/ccbot/hook.py` writes Claude hook events and session mapping files.

## Request/Response Lifecycles

Inbound user message (Telegram -> tmux):

1. PTB handler entry in `bot.py`.
2. `handlers/text_handler.py` validates context and resolves topic binding.
3. `session.py` maps `(user_id, thread_id)` -> `window_id`.
4. `tmux_manager.py` sends keys to the mapped window/pane.

Outbound agent output (provider transcript/event -> Telegram):

1. `session_monitor.py` polls tracked transcript/event sources incrementally.
2. Provider parser (`providers/*.py` + `transcript_parser.py`/`terminal_parser.py`) emits normalized updates.
3. `handlers/message_queue.py` enforces ordering, merge rules, and rate limits.
4. Telegram send helpers deliver messages and status updates.

Recovery flow (dead/missing session):

1. `handlers/status_polling.py` detects stale/dead bindings.
2. Recovery UI callbacks route through `handlers/recovery_callbacks.py`.
3. Session/window state is updated in `session.py` and persisted to `state.json`.

## Data Model and State Files

Config/state directory is `~/.ccbot` unless overridden by `CCBOT_DIR`.

- `state.json`: topic<->window bindings and window metadata.
- `session_map.json`: hook-generated tmux window -> session map.
- `events.jsonl`: append-only hook events stream.
- `monitor_state.json`: monitor byte offsets (session/event files).

Provider transcript sources (read-only):

- Claude: `~/.claude/projects/`
- Codex: `~/.codex/sessions/`
- Gemini: `~/.gemini/tmp/`

## Core Flow

Inbound (Telegram -> agent):

- message enters `bot.py` -> `handlers/text_handler.py` -> resolve bound window in `session.py` -> send keys via `tmux_manager.py`.

Outbound (agent -> Telegram):

- `session_monitor.py` reads transcript/event deltas -> provider parser transforms entries -> `handlers/message_queue.py` orders/rate-limits sends -> Telegram API.

## Design Constraints to Preserve

- one topic = one window mapping.
- internal identity keyed by tmux `window_id` (not window names).
- no parse-layer truncation; splitting only at Telegram send layer.
- per-window provider behavior and capability-gated UI.
- tmux operations stay centralized in `tmux_manager.py`; do not spread raw shell tmux calls across handlers.
- state mutations route through `session.py` + persistence helpers, not ad-hoc JSON writes.
