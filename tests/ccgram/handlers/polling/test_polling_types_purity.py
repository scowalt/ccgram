from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

POLLING_TYPES_PATH = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "ccgram"
    / "handlers"
    / "polling"
    / "polling_types.py"
)


def test_polling_types_does_not_load_polling_state() -> None:
    code = (
        "import sys; "
        "import ccgram.handlers.polling.polling_types; "
        "assert 'ccgram.handlers.polling.polling_state' not in sys.modules, "
        "'polling_types must NOT pull in polling_state at import time'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"polling_types load-time purity broken:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_polling_types_imports_are_restricted() -> None:
    source = POLLING_TYPES_PATH.read_text()
    tree = ast.parse(source)

    runtime_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            runtime_imports.append(node.module)
        elif isinstance(node, ast.Import):
            runtime_imports.extend(alias.name for alias in node.names)

    allowed = (
        "__future__",
        "collections",
        "collections.abc",
        "dataclasses",
        "typing",
        "ccgram.providers.base",
        "providers.base",
    )
    forbidden = [mod for mod in runtime_imports if mod not in allowed]
    assert not forbidden, (
        f"polling_types must only import stdlib + ccgram.providers.base; "
        f"forbidden top-level imports: {forbidden}"
    )
