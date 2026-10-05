from __future__ import annotations

import ast
from pathlib import Path

import pytest

ALLOWED_SESSION_MANAGER_ATTRS: frozenset[str] = frozenset(
    {
        "set_window_provider",
        "set_window_origin",
        "set_window_approval_mode",
        "set_window_cwd",
        "set_window_worktree",
        "set_display_name",
        "set_batch_mode",
        "set_tool_call_visibility",
        "cycle_batch_mode",
        "cycle_tool_call_visibility",
        "sync_display_names",
        "prune_stale_state",
        "prune_stale_window_states",
        "audit_state",
    }
)


HANDLERS_ROOT = Path(__file__).resolve().parents[2] / "src" / "ccgram" / "handlers"


def _iter_handler_files() -> list[Path]:
    return sorted(HANDLERS_ROOT.rglob("*.py"))


def _collect_session_manager_attrs(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if isinstance(value, ast.Name) and value.id == "session_manager":
            found.append((node.attr, node.lineno))
    return found


@pytest.mark.parametrize("path", _iter_handler_files(), ids=lambda p: p.name)
def test_handler_session_manager_access_is_write_or_admin(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations = [
        (attr, lineno)
        for attr, lineno in _collect_session_manager_attrs(tree)
        if attr not in ALLOWED_SESSION_MANAGER_ATTRS
    ]
    assert not violations, (
        f"{path.relative_to(HANDLERS_ROOT.parents[1])} accesses session_manager "
        "for reads — migrate to ccgram.window_query / ccgram.session_query. "
        f"Violations: {violations}"
    )


def test_allow_list_only_contains_real_session_manager_methods() -> None:
    from ccgram.session import SessionManager

    public = {name for name in dir(SessionManager) if not name.startswith("_")}
    missing = ALLOWED_SESSION_MANAGER_ATTRS - public
    assert not missing, (
        f"Allow-list contains entries not on SessionManager: {sorted(missing)}"
    )
