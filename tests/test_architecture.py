"""Keep the runtime dependency direction small and protect module ownership."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"


def test_runtime_import_graph_is_acyclic_and_independent_of_cli_and_tests():
    """Runtime modules may depend on each other, but never on entry points/tests."""
    modules = {path.stem: path for path in SOURCE.glob("*.py")}
    graph = {name: set() for name in modules}

    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = {node.module.split(".", 1)[0]}
            else:
                continue
            assert not imported.intersection({"tools", "tests"}), (
                f"runtime module {name} imports a CLI or test module"
            )
            graph[name].update(imported.intersection(modules))

    visiting = set()
    visited = set()

    def visit(module):
        if module in visiting:
            raise AssertionError(f"runtime import cycle includes {module}")
        if module in visited:
            return
        visiting.add(module)
        for dependency in graph[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in graph:
        visit(module)
