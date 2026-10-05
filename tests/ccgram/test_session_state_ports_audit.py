from __future__ import annotations

import ast
from pathlib import Path

import pytest

BANNED_FROM_CLAUDE_TASK_STATE: frozenset[str] = frozenset(
    {
        "get_claude_task_snapshot",
        "get_claude_wait_header",
    }
)

BANNED_ATTRIBUTE_READS: frozenset[str] = frozenset(
    {
        "has_snapshot",
        "get_snapshot",
        "get_claude_task_snapshot",
        "get_claude_wait_header",
    }
)

BANNED_SESSION_LIFECYCLE_ATTR_READS: frozenset[str] = frozenset({"resolve_session_id"})


HANDLERS_ROOT = Path(__file__).resolve().parents[2] / "src" / "ccgram" / "handlers"


def _iter_handler_files() -> list[Path]:
    return sorted(HANDLERS_ROOT.rglob("*.py"))


def _collect_banned_imports(
    tree: ast.AST, banned: frozenset[str]
) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name
            if name in banned:
                found.append((name, node.lineno))
    return found


@pytest.mark.parametrize("path", _iter_handler_files(), ids=lambda p: p.name)
def test_no_banned_read_imports_from_claude_task_state(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations = _collect_banned_imports(tree, BANNED_FROM_CLAUDE_TASK_STATE)
    assert not violations, (
        f"{path.relative_to(HANDLERS_ROOT.parents[1])} imports banned read "
        "symbols — use session_state_ports.live_session_state instead. "
        f"Violations: {violations}"
    )


def _collect_banned_attr_reads(
    tree: ast.AST, obj_name: str, banned_attrs: frozenset[str]
) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        if node.value.id == obj_name and node.attr in banned_attrs:
            found.append((node.attr, node.lineno))
    return found


@pytest.mark.parametrize("path", _iter_handler_files(), ids=lambda p: p.name)
def test_no_banned_read_attr_calls_on_claude_task_state(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations = _collect_banned_attr_reads(
        tree, "claude_task_state", BANNED_ATTRIBUTE_READS
    )
    assert not violations, (
        f"{path.relative_to(HANDLERS_ROOT.parents[1])} calls banned "
        "claude_task_state read method — use "
        "session_state_ports.live_session_state instead. "
        f"Violations: {violations}"
    )


@pytest.mark.parametrize("path", _iter_handler_files(), ids=lambda p: p.name)
def test_no_banned_attr_reads_on_session_lifecycle(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations = _collect_banned_attr_reads(
        tree, "session_lifecycle", BANNED_SESSION_LIFECYCLE_ATTR_READS
    )
    assert not violations, (
        f"{path.relative_to(HANDLERS_ROOT.parents[1])} calls "
        "session_lifecycle.resolve_session_id() — use "
        "session_state_ports.live_session_state.get_session_id() instead. "
        f"Violations: {violations}"
    )
