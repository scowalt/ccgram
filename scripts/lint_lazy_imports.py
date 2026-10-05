from __future__ import annotations

import ast
import json
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

_LAZY_COMMENT = re.compile(r"#\s*Lazy:")
_RESET_FN_NAME = re.compile(r"^_?reset.*_for_testing$")
_MIN_ARGV_FOR_PATHS = 2


def iter_python_files(root: Path) -> Iterator[Path]:
    yield from sorted(root.rglob("*.py"))


def _is_type_checking_test(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id == "TYPE_CHECKING":
        return True
    return isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING"


def _has_lazy_comment_above(source_lines: list[str], lineno: int) -> bool:
    idx = lineno - 2
    while idx >= 0:
        stripped = source_lines[idx].strip()
        if not stripped:
            idx -= 1
            continue
        if not stripped.startswith("#"):
            return False
        if _LAZY_COMMENT.search(stripped):
            return True
        idx -= 1
    return False


def _find_violations_in_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
    in_type_checking: bool,
) -> Iterator[tuple[int, str]]:
    if _RESET_FN_NAME.match(fn.name):
        return
    yield from _walk_block(fn.body, source_lines, in_type_checking)


def _sub_bodies(stmt: ast.stmt) -> Iterator[list[ast.stmt]]:
    if isinstance(stmt, ast.Try | ast.TryStar):
        yield stmt.body
        for handler in stmt.handlers:
            yield handler.body
        yield stmt.orelse
        yield stmt.finalbody
        return
    if isinstance(stmt, ast.If | ast.For | ast.AsyncFor | ast.While):
        yield stmt.body
        yield stmt.orelse
        return
    if isinstance(stmt, ast.With | ast.AsyncWith):
        yield stmt.body
        return
    if isinstance(stmt, ast.Match):
        for case in stmt.cases:
            yield case.body


def _check_import(
    stmt: ast.Import | ast.ImportFrom,
    source_lines: list[str],
    in_type_checking: bool,
) -> tuple[int, str] | None:
    if in_type_checking:
        return None
    if _has_lazy_comment_above(source_lines, stmt.lineno):
        return None
    snippet = (
        source_lines[stmt.lineno - 1].strip()
        if stmt.lineno - 1 < len(source_lines)
        else "<unknown>"
    )
    return (stmt.lineno, snippet)


def _walk_outer_block(
    body: Iterable[ast.stmt],
    source_lines: list[str],
    in_type_checking: bool,
) -> Iterator[tuple[int, str]]:
    for stmt in body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            yield from _find_violations_in_function(
                stmt, source_lines, in_type_checking
            )
            continue
        if isinstance(stmt, ast.ClassDef):
            yield from _walk_outer_block(stmt.body, source_lines, in_type_checking)
            continue
        if isinstance(stmt, ast.If) and _is_type_checking_test(stmt.test):
            yield from _walk_outer_block(stmt.body, source_lines, in_type_checking=True)
            yield from _walk_outer_block(stmt.orelse, source_lines, in_type_checking)
            continue
        for sub_body in _sub_bodies(stmt):
            yield from _walk_outer_block(sub_body, source_lines, in_type_checking)


def _walk_stmt(
    stmt: ast.stmt,
    source_lines: list[str],
    in_type_checking: bool,
) -> Iterator[tuple[int, str]]:
    if isinstance(stmt, ast.If) and _is_type_checking_test(stmt.test):
        yield from _walk_block(stmt.body, source_lines, in_type_checking=True)
        yield from _walk_block(stmt.orelse, source_lines, in_type_checking)
        return
    if isinstance(stmt, ast.Import | ast.ImportFrom):
        violation = _check_import(stmt, source_lines, in_type_checking)
        if violation is not None:
            yield violation
        return
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
        yield from _find_violations_in_function(stmt, source_lines, in_type_checking)
        return
    if isinstance(stmt, ast.ClassDef):
        yield from _walk_block(stmt.body, source_lines, in_type_checking)
        return
    sub = list(_sub_bodies(stmt))
    if sub:
        for sub_body in sub:
            yield from _walk_block(sub_body, source_lines, in_type_checking)
        return
    for child in ast.iter_child_nodes(stmt):
        yield from _walk_node(child, source_lines, in_type_checking)


def _walk_block(
    body: Iterable[ast.stmt],
    source_lines: list[str],
    in_type_checking: bool,
) -> Iterator[tuple[int, str]]:
    for stmt in body:
        yield from _walk_stmt(stmt, source_lines, in_type_checking)


def _walk_node(
    node: ast.AST,
    source_lines: list[str],
    in_type_checking: bool,
) -> Iterator[tuple[int, str]]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        yield from _find_violations_in_function(node, source_lines, in_type_checking)
        return
    if isinstance(node, ast.If) and _is_type_checking_test(node.test):
        yield from _walk_block(node.body, source_lines, in_type_checking=True)
        yield from _walk_block(node.orelse, source_lines, in_type_checking)
        return
    for child in ast.iter_child_nodes(node):
        yield from _walk_node(child, source_lines, in_type_checking)


def find_violations(path: Path) -> list[tuple[int, str]]:
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    violations = list(
        _walk_outer_block(tree.body, source_lines, in_type_checking=False)
    )
    repo = Path(__file__).resolve().parent.parent
    try:
        relative = path.resolve().relative_to(repo).as_posix()
    except ValueError:
        return violations
    declarations = json.loads(Path(__file__).with_name("lazy_imports.json").read_text())
    approved = {
        (entry["scope"], entry["import"])
        for entry in declarations
        if entry["path"] == relative
    }
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    imports = {
        node.lineno: node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
    }
    result = []
    for lineno, snippet in violations:
        node = imports[lineno]
        scope = []
        parent = parents.get(node)
        while parent:
            if isinstance(
                parent, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ):
                scope.append(parent.name)
            parent = parents.get(parent)
        identity = (".".join(reversed(scope)), ast.unparse(node))
        if identity not in approved:
            result.append((lineno, snippet))
    return result


def lint(roots: Iterable[Path]) -> list[tuple[Path, int, str]]:
    flat: list[tuple[Path, int, str]] = []
    for root in roots:
        for path in iter_python_files(root):
            for lineno, snippet in find_violations(path):
                flat.append((path, lineno, snippet))
    return flat


def main(argv: list[str]) -> int:
    if len(argv) < _MIN_ARGV_FOR_PATHS:
        repo_root = Path(__file__).resolve().parent.parent
        roots = [repo_root / "src" / "ccgram"]
    else:
        roots = [Path(arg).resolve() for arg in argv[1:]]
    violations = lint(roots)
    if not violations:
        print("lint-lazy: no undocumented in-function imports.")
        return 0
    for path, lineno, snippet in violations:
        print(f"{path}:{lineno}: undocumented in-function import: {snippet}")
    print(f"\nlint-lazy: {len(violations)} undocumented in-function import(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
