---
title: "Migrate scowalt user from ccbot to ccgram"
type: feat
status: active
date: 2026-04-13
---

# Migrate scowalt user from ccbot to ccgram

## Overview

Replace the running ccbot instance (six-ddc/ccmux fork, Python 3.13) with scowalt/ccgram (alexei-led/ccgram fork, Python 3.14+) for the scowalt user. These are **different codebases** — ccbot is a simpler fork with ~3 custom commits; ccgram is a heavily extended fork with providers, inter-agent messaging, hook events, shell support, and more.

## Current State

| Component | ccbot (current) | ccgram (target) |
|---|---|---|
| Codebase | six-ddc/ccmux fork | alexei-led/ccgram fork |
| Python | 3.13 | 3.14+ |
| Config dir | `~/.ccbot/` | `~/.ccgram/` |
| Tmux session | `ccbot` (9 windows) | `ccgram` (auto-detect) |
| Hooks | SessionStart only → `session_map.json` | 9 event types → `session_map.json` + `events.jsonl` |
| Hook path | `/home/scowalt/Code/ccbot/.venv/bin/ccbot hook` | `<ccgram venv>/python -m ccgram.main hook` |
| Env var prefix | `CCBOT_*` | `CCGRAM_*` (with `CCBOT_*` fallback) |

**ScoBot user** runs ccbot independently (separate user account, separate tmux server, separate config). Not affected.

## Proposed Solution

Atomic cutover: stop ccbot, configure ccgram, start ccgram. Estimated downtime: 1-3 minutes. Claude Code sessions in tmux windows continue running throughout — only the monitoring bot restarts.

## Pre-Migration: Prepare ccgram config

Create `~/.ccgram/.env` with migrated settings:

```bash
mkdir -p ~/.ccgram
```

```env
# ~/.ccgram/.env
TELEGRAM_BOT_TOKEN=<copy from ~/.ccbot/.env>
ALLOWED_USERS=8306816870
CCGRAM_CLAUDE_COMMAND=claude --dangerously-skip-permissions
```

**Env var changes from ccbot:**

| ccbot `.env` | ccgram `.env` | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `TELEGRAM_BOT_TOKEN` | Same key, same value (same bot) |
| `ALLOWED_USERS` | `ALLOWED_USERS` | Same |
| `CLAUDE_COMMAND=claude --dangerously-skip-permissions` | `CCGRAM_CLAUDE_COMMAND=claude --dangerously-skip-permissions` | **Renamed** — `CLAUDE_COMMAND` is silently ignored by ccgram |
| `CCBOT_SHOW_TOOL_CALLS=false` | *(remove)* | No equivalent in ccgram. ccgram uses batched message merging instead |
| `OPENAI_BASE_URL=http://100.97.172.16:8787/v1` | *(remove or reconfigure)* | Only needed if using LLM features (shell NL→command, completion summaries). Paired `OPENAI_API_KEY=unused` would cause auth failures |

## Migration Steps

### 1. Copy compatible state files

```bash
cp ~/.ccbot/state.json ~/.ccgram/state.json
cp ~/.ccbot/session_map.json ~/.ccgram/session_map.json
# Do NOT copy monitor_state.json — format differs between codebases
```

- `state.json`: Structurally compatible. ccgram's `WindowState.from_dict()` ignores unknown fields and defaults missing ones (`transcript_path`, `notification_mode`, `provider_name`, etc.)
- `session_map.json`: ccgram has a legacy `ccbot:` prefix fallback in `parse_session_map()`, so `ccbot:@12` keys are readable
- `monitor_state.json`: **Incompatible format** — ccgram will start fresh. This may cause a one-time burst of re-delivered transcript messages to Telegram topics

### 2. Stop ccbot

Kill the ccbot process in the `__main__` window. Do **not** kill the tmux session or other windows — Claude sessions keep running in their panes.

```bash
# From inside the ccbot tmux session, in the __main__ window:
# Ctrl-C to stop the bot, then Ctrl-\ if using restart.sh supervisor
```

### 3. Update Claude hooks

```bash
cd /home/scowalt/Code/ccgram
uv run ccgram hook --install
```

This replaces the single `ccbot hook` entry in `~/.claude/settings.json` with ccgram's 9 event type hooks. The hook installer auto-detects and removes legacy `ccbot hook` markers.

**Note:** Already-running Claude sessions will not fire SessionStart again. Hook-based event tracking (Stop, Notification, etc.) only activates for events that occur after installation. Terminal scraping works as fallback for monitoring existing sessions.

### 4. Rename tmux session

```bash
tmux rename-session -t ccbot ccgram
```

Window IDs (`@0`, `@12`, etc.) are stable across session renames. ccgram's startup `resolve_stale_ids()` matches persisted display names against live windows.

### 5. Start ccgram

```bash
cd /home/scowalt/Code/ccgram
./scripts/restart.sh start
```

This creates a `__main__` window in the renamed `ccgram` session and starts the supervisor loop. ccgram auto-detects the tmux session context.

### 6. Post-migration verification

```bash
ccgram doctor          # Validate hooks, tmux, state consistency
ccgram hook --status   # Confirm all 9 event types are installed
ccgram status          # Show running state
```

Then send a test message in one Telegram topic to verify the full round-trip.

## Rollback Plan

If ccgram fails to start:

```bash
tmux rename-session -t ccgram ccbot                              # Restore session name
cd /home/scowalt/Code/ccbot && uv run ccbot hook --install       # Restore ccbot hooks
# Restart ccbot in __main__ window
```

Claude sessions in tmux windows are unaffected throughout — only the monitoring bot changes.

## Known UX Changes After Migration

1. **Tool call output**: ccbot suppressed tool calls (`CCBOT_SHOW_TOOL_CALLS=false`). ccgram shows them in batched/merged format with tool_use/tool_result pairing. Output will feel noisier initially.
2. **More Telegram features**: ccgram adds `/send`, `/toolbar`, `/history`, `/sessions`, `/panes`, `/sync`, `/upgrade`, inter-agent messaging, live terminal view, voice messages, multi-provider support.
3. **Hook-based events**: Once sessions restart, ccgram gets instant notifications for Stop, Notification, SessionEnd, subagent activity, and more — instead of relying solely on terminal scraping.

## Acceptance Criteria

- [ ] ccbot process stopped for scowalt user
- [ ] `~/.ccgram/.env` configured with correct token and settings
- [ ] `state.json` and `session_map.json` copied to `~/.ccgram/`
- [ ] Claude hooks updated: `ccgram hook --status` shows all 9 event types
- [ ] Tmux session renamed to `ccgram`
- [ ] ccgram running and polling Telegram successfully
- [ ] Test message in one existing topic receives a response
- [ ] `ccgram doctor` passes with no errors
- [ ] ScoBot user unaffected (still running ccbot independently)

## Sources

- ccgram README migration section: `/home/scowalt/Code/ccgram/README.md:273-282`
- Config fallback logic: `/home/scowalt/Code/ccgram/src/ccgram/utils.py:87-118`
- Legacy env var mapping: `/home/scowalt/Code/ccgram/src/ccgram/config.py:24-30`
- Hook installer with legacy detection: `/home/scowalt/Code/ccgram/src/ccgram/hook.py:48-49`
- Session map legacy prefix: `/home/scowalt/Code/ccgram/src/ccgram/session_map.py`
