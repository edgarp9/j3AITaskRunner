from __future__ import annotations

import ast
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PYTHON_TARGETS = (
    PROJECT_ROOT / "main.py",
    PROJECT_ROOT / "build_release.py",
    PROJECT_ROOT / "app",
    PROJECT_ROOT / "domain",
    PROJECT_ROOT / "infra",
    PROJECT_ROOT / "ui",
)
PRODUCTION_LINE_LIMIT = 1500
LAYER_IMPORT_RULES = {
    "domain": {"app", "infra", "ui"},
    "app": {"ui"},
    "infra": {"ui"},
}


def _production_python_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for target in PRODUCTION_PYTHON_TARGETS:
        if target.is_file():
            files.append(target)
            continue
        files.extend(path for path in target.rglob("*.py") if path.is_file())
    return tuple(sorted(files))


def _physical_line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _module_parts_for_path(path: Path) -> tuple[str, ...]:
    relative = path.relative_to(PROJECT_ROOT).with_suffix("")
    return relative.parts


def _absolute_import_root(
    node: ast.ImportFrom,
    *,
    current_module_parts: tuple[str, ...],
) -> str | None:
    if node.level == 0:
        if node.module is None:
            return None
        return node.module.split(".", maxsplit=1)[0]

    package_parts = current_module_parts[:-1]
    keep_count = len(package_parts) - node.level + 1
    if keep_count < 0:
        return None
    resolved_parts = package_parts[:keep_count]
    if node.module:
        resolved_parts += tuple(node.module.split("."))
    if not resolved_parts:
        return None
    return resolved_parts[0]


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    current_module_parts = _module_parts_for_path(path)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            root = _absolute_import_root(
                node,
                current_module_parts=current_module_parts,
            )
            if root is not None:
                roots.add(root)
    return roots


class StaticQualityTests(unittest.TestCase):
    def test_production_python_files_do_not_exceed_line_limit(self) -> None:
        oversized_files = [
            (path.relative_to(PROJECT_ROOT).as_posix(), _physical_line_count(path))
            for path in _production_python_files()
            if _physical_line_count(path) > PRODUCTION_LINE_LIMIT
        ]

        self.assertEqual(
            [],
            oversized_files,
            f"Production Python files must stay within {PRODUCTION_LINE_LIMIT} lines.",
        )

    def test_layer_imports_keep_architecture_direction(self) -> None:
        violations: list[str] = []
        for source_layer, forbidden_roots in LAYER_IMPORT_RULES.items():
            for path in sorted((PROJECT_ROOT / source_layer).rglob("*.py")):
                imported_forbidden_roots = _import_roots(path) & forbidden_roots
                if imported_forbidden_roots:
                    relative_path = path.relative_to(PROJECT_ROOT).as_posix()
                    forbidden = ", ".join(sorted(imported_forbidden_roots))
                    violations.append(f"{relative_path} imports forbidden layer(s): {forbidden}")

        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
