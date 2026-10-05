from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "ccgram"

ALLOWED_FILES: frozenset[Path] = frozenset(
    {
        SRC_ROOT / "multiplexer" / "tmux.py",
        SRC_ROOT / "hook.py",
    }
)

_GET_FG_ARGS = re.compile(r"\bget_foreground_args\b")

_PS_T_ARGV = re.compile(r"""['"]ps['"]\s*,\s*['"]-t['"]""")


def _offenders_in_source(source: str, rel: str) -> list[tuple[int, str]]:
    offenders: list[tuple[int, str]] = []

    tree = ast.parse(source, filename=rel)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "pane_tty":
            offenders.append(
                (node.lineno, "reads .pane_tty (use Multiplexer.foreground)")
            )

    for lineno, line in enumerate(source.splitlines(), start=1):
        if _GET_FG_ARGS.search(line):
            offenders.append((lineno, "references get_foreground_args (removed)"))
        if _PS_T_ARGV.search(line):
            offenders.append((lineno, "forks `ps -t` (use the foreground seam)"))

    return offenders


def _audited_files() -> list[Path]:
    return sorted(p for p in SRC_ROOT.rglob("*.py") if p not in ALLOWED_FILES)


@pytest.mark.parametrize(
    "path", _audited_files(), ids=lambda p: str(p.relative_to(SRC_ROOT))
)
def test_no_tty_dependency_outside_backend(path: Path) -> None:
    offenders = _offenders_in_source(path.read_text(encoding="utf-8"), str(path))
    assert not offenders, (
        f"{path.relative_to(SRC_ROOT)} reaches past the multiplexer seam for "
        "foreground/tty info:\n"
        + "\n".join(f"  line {ln}: {why}" for ln, why in offenders)
        + "\nGet the foreground process via Multiplexer.foreground(window_id) and "
        "gate tty-dependent branches on caps.exposes_pane_tty instead."
    )


def test_allow_list_files_exist() -> None:
    for path in ALLOWED_FILES:
        assert path.is_file(), f"Allow-listed file missing: {path}"


def test_audit_actually_walks_files() -> None:
    assert len(_audited_files()) > 50


def test_audit_flags_planted_pane_tty_read() -> None:
    planted = "def leak(window):\n    return window.pane_tty\n"
    offenders = _offenders_in_source(planted, "fake.py")
    assert [why for _, why in offenders] == [
        "reads .pane_tty (use Multiplexer.foreground)"
    ]


def test_audit_flags_planted_get_foreground_args() -> None:
    planted = "from .process_detection import get_foreground_args\nx = get_foreground_args(tty)\n"
    offenders = _offenders_in_source(planted, "fake.py")
    assert any("get_foreground_args" in why for _, why in offenders)


def test_audit_flags_planted_ps_t_argv() -> None:
    planted = 'args = ["ps", "-t", tty, "-o", "command="]\n'
    offenders = _offenders_in_source(planted, "fake.py")
    assert [why for _, why in offenders] == ["forks `ps -t` (use the foreground seam)"]


def test_audit_allows_capability_gate() -> None:
    clean = (
        "def detect(caps, window):\n"
        "    if not caps.exposes_pane_tty:\n"
        "        return None\n"
        "    return window\n"
    )
    assert _offenders_in_source(clean, "fake.py") == []


def test_audit_allows_prose_and_field_definition() -> None:
    clean = (
        '"""Foreground via ``pane_tty`` + ``ps -t <tty>`` on tmux."""\n'
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class WindowRef:\n"
        '    pane_tty: str = ""\n'
    )
    assert _offenders_in_source(clean, "fake.py") == []
