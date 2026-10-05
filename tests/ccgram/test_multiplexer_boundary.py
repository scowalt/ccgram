from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "ccgram"

FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "ccgram.multiplexer.tmux",
    "ccgram.multiplexer.herdr",
    "ccgram.tmux_manager",
    "libtmux",
)

EXEMPT_FILES: frozenset[Path] = frozenset(
    {
        SRC_ROOT / "bootstrap.py",
        SRC_ROOT / "main.py",
    }
)


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_PREFIXES
    )


def _package_parts(path: Path) -> list[str]:
    rel = path.relative_to(SRC_ROOT.parent).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    else:
        parts.pop()
    return parts


def _resolve(path: Path, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    pkg = _package_parts(path)
    base = pkg[: len(pkg) - (level - 1)]
    if module:
        return ".".join([*base, module])
    return ".".join(base)


def _forbidden_imports_in_source(source: str, path: Path) -> list[str]:
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden(alias.name):
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve(path, node.level or 0, node.module)
            if _is_forbidden(resolved):
                found.append(resolved)
            else:
                for alias in node.names:
                    candidate = f"{resolved}.{alias.name}" if resolved else alias.name
                    if _is_forbidden(candidate):
                        found.append(candidate)
    return found


def _audited_files() -> list[Path]:
    files: list[Path] = []
    for path in SRC_ROOT.rglob("*.py"):
        if "multiplexer" in path.relative_to(SRC_ROOT).parts:
            continue
        if path in EXEMPT_FILES:
            continue
        files.append(path)
    return sorted(files)


@pytest.mark.parametrize("path", _audited_files(), ids=lambda p: str(p.name))
def test_no_caller_imports_a_concrete_backend(path: Path) -> None:
    offenders = _forbidden_imports_in_source(path.read_text(), path)
    assert not offenders, (
        f"{path} imports a concrete multiplexer backend: {offenders}. "
        "Depend on the ``multiplexer`` proxy / ``multiplexer.base`` Protocol "
        "instead of a backend, libtmux, or the deleted tmux_manager module."
    )


def test_audit_flags_a_planted_direct_backend_import() -> None:
    planted = "from ccgram.multiplexer.tmux import tmux_manager\n"
    offenders = _forbidden_imports_in_source(
        planted, SRC_ROOT / "handlers" / "fake_handler.py"
    )
    assert offenders == ["ccgram.multiplexer.tmux"]


def test_audit_flags_planted_libtmux_and_legacy_imports() -> None:
    planted = "import libtmux\nfrom ccgram.tmux_manager import tmux_manager\n"
    offenders = _forbidden_imports_in_source(
        planted, SRC_ROOT / "handlers" / "fake_handler.py"
    )
    assert "libtmux" in offenders
    assert "ccgram.tmux_manager" in offenders


def test_audit_allows_the_neutral_seam_surface() -> None:
    clean = (
        "from ...multiplexer import multiplexer as tmux_manager\n"
        "from ...multiplexer.base import WindowRef, PaneInfo\n"
        "from ...multiplexer.vim_state import has_insert_indicator\n"
        "from ...multiplexer.window_ops import send_to_window\n"
    )
    offenders = _forbidden_imports_in_source(
        clean, SRC_ROOT / "handlers" / "live" / "fake_handler.py"
    )
    assert offenders == []


NEW_TOPIC_MODULES: tuple[str, ...] = (
    "topic_creation_draft",
    "provider_mode_callbacks",
    "workspace_callbacks",
    "window_launch_service",
)


@pytest.mark.parametrize("module_name", NEW_TOPIC_MODULES)
def test_new_topic_submodule_does_not_import_concrete_backend(
    module_name: str,
) -> None:
    path = SRC_ROOT / "handlers" / "topics" / f"{module_name}.py"
    assert path.exists(), f"Expected {path} to exist"
    offenders = _forbidden_imports_in_source(path.read_text(), path)
    assert not offenders, (
        f"{module_name}.py imports a concrete multiplexer backend: {offenders}. "
        "Use the multiplexer proxy or multiplexer.base types instead."
    )


def test_window_launch_service_does_not_import_window_store() -> None:
    import ast

    path = SRC_ROOT / "handlers" / "topics" / "window_launch_service.py"
    source = path.read_text()
    tree = ast.parse(source)
    store_symbols = frozenset(
        {"window_store", "get_window_store", "install_window_store", "WindowStateStore"}
    )
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.endswith("window_state_store"):
                for alias in node.names:
                    if alias.name in store_symbols:
                        hits.append(alias.name)
    assert not hits, (
        f"window_launch_service.py imports the raw window store: {hits}. "
        "Import only CCGRAM_CREATED_WINDOW_ORIGIN (a constant), not the store itself."
    )
