# Modularity Review

**Scope**: Full codebase — `src/ccgram/` (~28,800 LOC across 55 Python modules)
**Date**: 2026-03-31
**Context**: Fourth review. Reviews #1–#2 (2026-03-28, 2026-03-29) resolved 4 of 5 original issues. Review #3 (2026-03-30) covered the inter-agent messaging feature. This review reassesses the full codebase after the messaging subsystem stabilized and the `TopicStateRegistry` matured.

## Executive Summary

ccgram manages AI coding agents from Telegram via tmux — each Forum topic binds to one tmux window running Claude Code, Codex, Gemini, or a shell. The codebase's modularity has improved steadily across four reviews: the original 2,018-line `bot.py` monolith is down to 1,069 lines, the fragmented per-topic state cleanup (14+ lazy imports in `cleanup.py`) was replaced by a self-registering `TopicStateRegistry` with 20 callbacks across 16 modules, and the unused `protocols.py` was removed. The inter-agent messaging subsystem is well-bounded — `mailbox.py` has zero ccgram imports, `msg_skill.py` is fully independent, and `msg_delivery.py` cleanly separates state from orchestration. The most significant remaining concern is `polling_coordinator.py`, which at 972 lines and 33 dependencies (including 5 strategy-internal constants and broker delivery orchestration) is becoming the second hub in the system — absorbing responsibilities faster than the strategy decomposition can contain them.

---

## Previous Issues: Resolution Status

| #   | Issue (Review #2)                          | Severity    | Status          | Evidence                                                                                                                      |
| --- | ------------------------------------------ | ----------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| 1   | Fragmented Per-Topic State                 | SIGNIFICANT | **✅ Resolved** | `TopicStateRegistry` with 20 self-registered callbacks across 16 modules; `cleanup.py` reduced from 14+ lazy imports to 1     |
| 2   | Unused Protocol Interfaces                 | MODERATE    | **✅ Resolved** | `protocols.py` removed entirely — no file, no imports                                                                         |
| 3   | polling_coordinator Multi-Domain Knowledge | MODERATE    | **⚠️ Partial**  | 5 strategy constants still imported; coordinator now also drives broker delivery cycle and mailbox sweep (972 lines, 33 deps) |

### Resolution Details

**Issue #1 — Fragmented State**: The `TopicStateRegistry` is a textbook application of the [Observer pattern](https://coupling.dev/posts/core-concepts/modularity/) for cleanup coordination. Modules register their own cleanup functions via `@topic_state.register("window")` at import time. `cleanup.py` calls `topic_state.clear_all(...)` — one line replaces what was previously 14+ lazy imports and per-module cleanup calls. The key improvement is that **adding new per-topic state no longer requires changes to `cleanup.py`** — the decorator self-registers. This eliminated the entire class of "forgot to add cleanup" bugs.

**Issue #2 — Unused Protocols**: The recommendation was "either adopt or remove." The code was removed, eliminating dead abstraction. The 21 handler modules continue importing `session_manager` directly, which is acceptable given the single-developer [distance](https://coupling.dev/posts/dimensions-of-coupling/distance/).

**Issue #3 — Coordinator Knowledge**: Partially resolved. The strategy decomposition remains effective for state ownership, but the coordinator continues to absorb new responsibilities. See Issue #1 below.

---

## Coupling Overview (Current State)

| Integration                         | [Strength](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/)   | [Distance](https://coupling.dev/posts/dimensions-of-coupling/distance/) | [Volatility](https://coupling.dev/posts/dimensions-of-coupling/volatility/) | [Balanced?](https://coupling.dev/posts/core-concepts/balance/) |
| ----------------------------------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | --------------------------------------------------------------------------- | -------------------------------------------------------------- |
| Hook System → Monitoring            | [Contract](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/)   | High (separate processes)                                               | Low                                                                         | ✅ Yes                                                         |
| Provider Protocol → Consumers       | [Contract](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/)   | Low (same package)                                                      | Low-Medium                                                                  | ✅ Yes                                                         |
| LLM/Whisper → Consumers             | [Contract](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/)   | Low (same package)                                                      | Low                                                                         | ✅ Yes                                                         |
| Callback Registry → Handlers        | [Contract](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/)   | Low (same package)                                                      | Medium                                                                      | ✅ Yes                                                         |
| TopicStateRegistry → 16 modules     | [Contract](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/)   | Low (same package)                                                      | High                                                                        | ✅ Yes                                                         |
| Mailbox → Messaging subsystem       | [Contract](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/)   | Low (same package)                                                      | Medium                                                                      | ✅ Yes                                                         |
| DeliveryStrategy → Broker/Telegram  | [Contract](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/)   | Low (same package)                                                      | Medium                                                                      | ✅ Yes                                                         |
| Polling Coordinator → 8+ domains    | [Functional](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/) | Low (same package)                                                      | High                                                                        | **❌ No**                                                      |
| msg_broker → Mailbox path internals | [Model](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/)      | Low (same package)                                                      | Medium                                                                      | **⚠️ Borderline**                                              |
| Core modules → handlers/ registry   | [Contract](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/)   | Low (same package, wrong direction)                                     | Low                                                                         | **⚠️ Borderline**                                              |
| SessionManager → 21 handlers        | [Functional](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/) | Low (same developer)                                                    | Medium                                                                      | ✅ Yes (single-team)                                           |

---

## Issue: polling_coordinator Absorbing Responsibilities

**Integration**: `polling_coordinator.py` → strategies, providers, sessions, tmux, interactive UI, shell, topic lifecycle, recovery, message queue, **message broker**
**Severity**: SIGNIFICANT

### Knowledge Leakage

`polling_coordinator.py` (972 lines) has 33 dependencies: 18 top-level imports + 15 lazy imports inside function bodies. It directly imports 5 constants from `polling_strategies.py` that are strategy implementation details:

| Constant             | Used for                                            | Strategy owner           |
| -------------------- | --------------------------------------------------- | ------------------------ |
| `ACTIVITY_THRESHOLD` | Deciding if a window is "active" based on timestamp | `TerminalStatusStrategy` |
| `MAX_PROBE_FAILURES` | Deciding when to stop probing a window              | `TerminalStatusStrategy` |
| `PANE_COUNT_TTL`     | Caching multi-pane layout detection                 | `InteractiveUIStrategy`  |
| `STARTUP_TIMEOUT`    | Grace period for newly created windows              | `TerminalStatusStrategy` |
| `TYPING_INTERVAL`    | Cooldown between typing action sends                | `TerminalStatusStrategy` |

These constants define **when** strategies should act — that knowledge belongs inside the strategies, not in the coordinator. The coordinator uses them to make decisions that the strategies should make themselves.

Since Review #2, the coordinator also absorbed broker delivery: `_run_broker_cycle()` (lines 833–852) calls `broker_delivery_cycle()` every 2 seconds, and `_run_mailbox_sweep()` (lines 855–862) runs every 5 minutes. This adds message delivery as a sixth domain the coordinator manages alongside terminal status, interactive UI, topic lifecycle, shell relay, and recovery.

### Complexity Impact

The coordinator's 33 dependencies mean a developer working on any change to the polling loop must hold 8+ domain concepts in working memory simultaneously. The strategy decomposition was intended to reduce this by letting each strategy own its decisions — but importing private constants re-couples the coordinator to strategy internals, making the abstraction boundary [leaky](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/).

When a strategy's threshold needs to change (e.g., `STARTUP_TIMEOUT` increases for slower providers), the change is mechanically local (one constant), but the reasoning requires understanding how the coordinator uses it — defeating the encapsulation.

### Cascading Changes

1. **Adding a new provider status pattern** (e.g., a provider with its own startup detection): requires modifying both the strategy and the coordinator, because the coordinator makes startup decisions using `STARTUP_TIMEOUT` directly.

2. **Adding a new poll-loop responsibility** (e.g., a health check system): follows the established pattern of adding another lazy import and another code block in the main loop. The coordinator grows linearly with each new cross-cutting concern.

3. **Changing broker delivery timing**: the coordinator imports `BROKER_CYCLE_INTERVAL` and `SWEEP_INTERVAL` from `msg_broker` — the timing decisions are co-owned across modules rather than encapsulated.

### Recommended Improvement

Push decision-making into strategies by eliminating constant exports:

1. **Strategies should expose decision methods, not thresholds.** Instead of exporting `ACTIVITY_THRESHOLD`, `TerminalStatusStrategy` should expose `is_recently_active(window_id) -> bool`. Instead of exporting `STARTUP_TIMEOUT`, it should expose `is_in_startup_grace(window_id) -> bool`. The coordinator calls these methods without knowing the threshold values.

2. **Broker delivery should be a strategy.** Extract `_run_broker_cycle` and `_run_mailbox_sweep` into a `BrokerStrategy` class in `polling_strategies.py` (or a new module). The coordinator calls `broker_strategy.poll()` like it calls other strategies, without importing broker internals.

**Trade-off**: This requires defining 5+ new method signatures on the strategy classes. The benefit is that the coordinator drops from 33 to ~25 dependencies and no longer needs to know strategy-internal constants. Each strategy becomes a fully encapsulated unit testable without coordinator context.

---

## Issue: TopicStateRegistry Layer Inversion

**Integration**: `tmux_manager.py`, `msg_discovery.py`, `spawn_request.py`, `providers/process_detection.py` → `handlers/topic_state_registry.py`
**Severity**: MODERATE

### Knowledge Leakage

The `TopicStateRegistry` is logically core infrastructure — it's a zero-dependency cleanup registry (only imports `structlog` and stdlib). Yet it lives in `handlers/`, creating an upward dependency where 4 core modules import from the handler layer:

```
src/ccgram/tmux_manager.py          → from .handlers.topic_state_registry import topic_state
src/ccgram/msg_discovery.py         → from .handlers.topic_state_registry import topic_state
src/ccgram/spawn_request.py         → from .handlers.topic_state_registry import topic_state
src/ccgram/providers/process_detection.py → from ..handlers.topic_state_registry import topic_state
```

The import direction violates the expected layering: `handlers/` depends on core modules, not the reverse. Core modules should not reach into `handlers/` for infrastructure.

### Complexity Impact

For a new developer (or AI agent) reading the dependency graph, seeing core modules import from `handlers/` suggests either circular dependencies or blurred layer boundaries. The actual coupling is [contract-level](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/) (just a decorator registration) and the [volatility](https://coupling.dev/posts/dimensions-of-coupling/volatility/) is low (the decorator API is stable), so the [balance rule](https://coupling.dev/posts/core-concepts/balance/) is satisfied. The issue is architectural clarity, not coupling mechanics.

### Cascading Changes

No cascading change risk — the decorator API is stable and unlikely to change. This is a structural hygiene issue, not a fragility risk.

### Recommended Improvement

Move `topic_state_registry.py` from `handlers/` to the top-level `src/ccgram/` package. The file has zero handler-specific dependencies and is consumed by modules at every layer. All 16 consumer imports change from `from .handlers.topic_state_registry` to `from .topic_state_registry` (for sibling imports) or `from ..topic_state_registry` (for handler imports).

**Trade-off**: Purely mechanical refactoring (~20 import path changes). No behavioral change. Aligns the physical structure with the logical dependency graph.

---

## Issue: Messaging Subsystem Boundary Bleed

**Integration**: `msg_broker.py` → `mailbox.py` path internals; `msg_spawn.py` → `topic_orchestration.py`; `msg_cmd.py` → `msg_discovery._detect_branch`
**Severity**: MODERATE

### Knowledge Leakage

Three places where the messaging subsystem's boundaries are not clean:

**1. Path convention duplication.** `msg_broker.write_delivery_file()` constructs `mailbox_dir / sanitize_dir_name(window_id) / "tmp"` — duplicating the path convention that `Mailbox._inbox_dir()` owns internally. If `Mailbox` changes its directory layout, `write_delivery_file` breaks silently (files land in the wrong directory).

**2. msg_spawn crosses into topic orchestration.** `handle_spawn_approval()` imports `topic_orchestration.collect_target_chats` and `create_topic_in_chat` — two complex functions from the bot's topic lifecycle layer. This pulls heavyweight Telegram orchestration logic (group chat discovery, topic creation, emoji setup) into the messaging subsystem, creating [functional coupling](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/) between messaging and bot lifecycle.

**3. Private function import.** `msg_cmd.py` imports `msg_discovery._detect_branch` (underscore-prefixed) — accessing an implementation detail across module boundaries. The design docs already acknowledge this should be public.

**4. Missing cleanup registration.** `msg_telegram._loop_alert_pairs` (module-level dict, max 100 entries) is not registered with `TopicStateRegistry`. When a window closes, its loop alert entries remain until evicted by the LRU cap. This is a minor state leak, but it breaks the pattern established by the other 20 registered callbacks.

### Complexity Impact

The path duplication (#1) is the most operationally risky: the [shared model](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/) of "how mailbox directories are structured" lives in two places, and divergence produces silent failures (messages written to wrong paths, never delivered).

The `msg_spawn` → `topic_orchestration` dependency (#2) means the messaging subsystem cannot be understood or tested without pulling in the full Telegram bot lifecycle — increasing the cognitive load for changes to spawn approval logic.

### Cascading Changes

1. **Changing mailbox directory layout** (e.g., adding a version subdirectory): requires updating both `Mailbox._inbox_dir()` and `msg_broker.write_delivery_file()`. The second change is easy to miss.

2. **Modifying topic creation flow** (e.g., adding a confirmation step): cascades into `msg_spawn.handle_spawn_approval()` because it calls `create_topic_in_chat()` directly.

### Recommended Improvement

1. **Add `Mailbox.delivery_path(window_id, msg_id) -> Path`** — a public method that creates the delivery file path and ensures the directory exists. `msg_broker.write_delivery_file()` calls this instead of constructing the path itself. Eliminates the shared path model.

2. **Extract a spawn executor interface.** `msg_spawn` should call a single function like `execute_spawn(request) -> SpawnResult` that encapsulates the topic creation details. This could be a callback registered during bot setup, or a simple function in `topic_orchestration` that packages `collect_target_chats` + `create_topic_in_chat` into one entry point.

3. **Make `_detect_branch` public** — rename to `detect_branch`. Single-line change.

4. **Register `_loop_alert_pairs` cleanup** — add `@topic_state.register("qualified")` to a `clear_loop_alerts` function in `msg_telegram.py`.

**Trade-off**: Items 1, 3, and 4 are trivial fixes. Item 2 requires a small interface extraction but significantly improves the messaging subsystem's independence.

---

## Well-Balanced Integrations

These integrations demonstrate good [modularity](https://coupling.dev/posts/core-concepts/modularity/) and should be preserved:

### TopicStateRegistry (New)

The self-registering cleanup pattern with 20 callbacks across 16 modules is the review series' biggest win. Adding per-topic state is now a 1-step operation (add the decorator). `cleanup.py` dropped from 14+ lazy imports to a single `topic_state.clear_all()` call. The four cleanup scopes (`topic`, `window`, `qualified`, `chat`) handle the inconsistent key types that Review #2 flagged.

### Messaging Subsystem Core

`mailbox.py` (585 lines) has zero ccgram imports — it's a pure file-based message store. `msg_delivery.py` cleanly separates delivery state (rate limits, loop detection, paused peers) from the broker's delivery logic. `msg_skill.py` is fully independent (zero ccgram imports). The `Message` dataclass flows through the system as the universal [contract](https://coupling.dev/posts/dimensions-of-coupling/integration-strength/).

### Provider Protocol

Still the strongest boundary. Four providers implement 15+ methods behind `AgentProvider`. The `JsonlProvider` base class shares JSONL logic between Codex and Gemini. `codex_status.py` and `codex_format.py` live inside `providers/`. No consumer checks `capabilities.name`.

### Leaf Modules

`thread_router.py` (0 ccgram imports), `user_preferences.py` (0 ccgram imports), `state_persistence.py` (1 import), `terminal_parser.py`, `screen_buffer.py`, `screenshot.py`, `entity_formatting.py`, `window_resolver.py`, `monitor_state.py` — all pure utilities with minimal coupling.

---

## Modularity Scorecard

| Dimension                        | Review #2 (2026-03-29)                                                 | Review #4 (2026-03-31)                                    | Trend                      |
| -------------------------------- | ---------------------------------------------------------------------- | --------------------------------------------------------- | -------------------------- |
| Largest module                   | `session.py` (1,476 lines)                                             | `session.py` (1,423 lines)                                | ⬆️ Improved                |
| Modules > 1,000 lines            | 2 (`session.py`, `bot.py` at 1,050)                                    | 2 (`session.py` at 1,423, `bot.py` at 1,069)              | ➡️ Stable                  |
| Max import count (single module) | ~20 (`bot.py`)                                                         | 33 (`bot.py`)                                             | ⬇️ Higher (handler growth) |
| Critical issues                  | 0                                                                      | 0                                                         | ➡️ Stable                  |
| Significant issues               | 1                                                                      | 1                                                         | ➡️ Stable                  |
| Moderate issues                  | 2                                                                      | 2                                                         | ➡️ Stable                  |
| Balanced integrations            | 6 (hook, provider, LLM/whisper, callbacks, strategies, shell contract) | 7 (+TopicStateRegistry, +messaging core)                  | ⬆️ Improved                |
| Late import workarounds          | 7 (in cleanup.py alone)                                                | 5 (cleanup.py), 15 (polling_coordinator)                  | ⬇️ Shifted, not eliminated |
| TopicStateRegistry callbacks     | Not measured                                                           | 20 across 16 modules                                      | 🔍 Newly measured          |
| Messaging module independence    | N/A                                                                    | 2 of 9 modules have 0 ccgram imports (mailbox, msg_skill) | 🔍 Newly measured          |

---

## Priority Recommendations

1. **Push decisions into strategies** (addresses Issue #1 — coordinator sprawl). Replace 5 exported constants with strategy methods (`is_recently_active`, `is_in_startup_grace`, etc.). Extract broker delivery as a strategy. Target: coordinator drops below 800 lines and 25 dependencies.

2. **Move `topic_state_registry.py` to core** (addresses Issue #2 — layer inversion). Mechanical refactoring: ~20 import path changes, zero behavioral change. Aligns physical structure with the logical dependency graph.

3. **Add `Mailbox.delivery_path()` and register `_loop_alert_pairs` cleanup** (addresses Issue #3 — messaging boundary bleed). Quick wins that eliminate path duplication and state leak.

---

_This analysis was performed using the [Balanced Coupling](https://coupling.dev) model by [Vlad Khononov](https://vladikk.com)._
