"""Regression guard for the open-source trust-layer boundary.

These modules are designated "open" in docs/vision_and_mission.md /
CLAUDE.md's "Open-Source Boundary" section — they must never import anything
from the proprietary product layer (exchange adapters, exchange clients,
email rendering), since that would either leak proprietary code into a
future public mirror or break the mirrored copy at import time.

AST-based rather than a plain grep so it can't be fooled by import statements
inside comments/strings, and so it resolves the actual dotted module path
being imported rather than just matching substrings.

Checks TRANSITIVELY, not just each open file's own import statements. A
proprietary import can hide behind an intermediate module that isn't itself
forbidden — e.g. aurono/analytics/benchmarks.py importing aurono.marketdata.
candle_store (fine on its own) which in turn imports aurono.exchange.
bitvavo_client (forbidden). Only checking direct imports missed exactly this
case in practice (found 2026-09-02 while dry-running the public mirror).
"""
import ast
from pathlib import Path
from typing import Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

OPEN_DESIGNATED_DIRS = [
    "aurono/domain",
    "aurono/events",
    "aurono/ledger",
    "aurono/analytics",
    "aurono/execution/sizing",
]
OPEN_DESIGNATED_FILES = [
    "aurono/execution/commands.py",
    "aurono/execution/events.py",
    "aurono/reports/generator.py",
    "aurono/db/connect.py",
]

# Prefixes that must never appear anywhere in an open-designated module's
# transitive import closure.
FORBIDDEN_PREFIXES = (
    "aurono.execution.adapters",
    "aurono.exchange",
    "aurono.reports.renderer",
)


def _open_designated_files():
    files = []
    for d in OPEN_DESIGNATED_DIRS:
        files.extend(sorted((REPO_ROOT / d).rglob("*.py")))
    for f in OPEN_DESIGNATED_FILES:
        files.append(REPO_ROOT / f)
    return files


def _file_to_module(path: Path) -> str:
    """Dotted module name for a file, e.g. aurono/domain/math.py -> aurono.domain.math."""
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _containing_package(path: Path) -> str:
    """Dotted package name a relative import inside `path` is resolved against."""
    module = _file_to_module(path)
    if path.name == "__init__.py":
        return module
    parts = module.split(".")
    return ".".join(parts[:-1])


def _resolve_relative_import(node: ast.ImportFrom, importing_path: Path) -> Optional[str]:
    """Resolve a `from . import x` / `from ..pkg import y` style import
    (node.level > 0) against the importing file's own package, since these
    never carry the "aurono." prefix on their own."""
    package = _containing_package(importing_path)
    pkg_parts = package.split(".") if package else []
    up = node.level - 1  # level=1 means "current package"
    if up > 0:
        pkg_parts = pkg_parts[:-up] if up <= len(pkg_parts) else []
    base = ".".join(pkg_parts)
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base or None


def _imported_modules(path: Path) -> set:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    modules.add(node.module)
            else:
                resolved = _resolve_relative_import(node, path)
                if resolved:
                    modules.add(resolved)
    return modules


def _resolve_aurono_module(module: str) -> Optional[Path]:
    """File path for a dotted aurono.* module, if it exists in the repo."""
    if module != "aurono" and not module.startswith("aurono."):
        return None
    parts = module.split(".")
    candidate_file = REPO_ROOT.joinpath(*parts).with_suffix(".py")
    if candidate_file.is_file():
        return candidate_file
    candidate_pkg = REPO_ROOT.joinpath(*parts, "__init__.py")
    if candidate_pkg.is_file():
        return candidate_pkg
    return None


def _transitive_import_chains(entry_path: Path) -> Dict[str, List[str]]:
    """BFS over the aurono.* import graph reachable from entry_path.

    Returns {imported_module: chain}, where chain is the list of module
    names hopped through to first reach it from entry_path (both aurono.*
    modules, which get expanded further, and terminal non-aurono modules,
    which don't)."""
    chains: Dict[str, List[str]] = {}
    queue: List[tuple] = [(entry_path, [])]
    seen_paths = {entry_path}
    while queue:
        current_path, chain_so_far = queue.pop(0)
        for mod in sorted(_imported_modules(current_path)):
            new_chain = chain_so_far + [mod]
            if mod not in chains:
                chains[mod] = new_chain
            resolved = _resolve_aurono_module(mod)
            if resolved is not None and resolved not in seen_paths:
                seen_paths.add(resolved)
                queue.append((resolved, new_chain))
    return chains


_FILES = _open_designated_files()


@pytest.mark.parametrize(
    "path", _FILES, ids=[str(p.relative_to(REPO_ROOT)) for p in _FILES]
)
def test_open_module_never_imports_proprietary(path: Path):
    """Every module designated open in the trust-layer split must be
    transitively import-clean of the proprietary product layer — see
    docs/vision_and_mission.md "Trust by Architecture". This is the guard
    that keeps a future public mirror buildable and prevents proprietary
    code (exchange credentials handling, adapters, email rendering) from
    leaking into it, even through an intermediate module that isn't itself
    forbidden."""
    chains = _transitive_import_chains(path)
    violations = {
        mod: chain
        for mod, chain in chains.items()
        if any(mod == prefix or mod.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)
    }
    assert not violations, (
        f"{path.relative_to(REPO_ROOT)} transitively imports proprietary module(s): "
        + "; ".join(f"{mod} (via {' -> '.join(chain)})" for mod, chain in sorted(violations.items()))
    )


def test_open_designated_scope_is_not_empty():
    """Sanity check on the test itself — if this list ever comes back empty
    (e.g. a path renamed and the constants above weren't updated), the
    parametrized test above would silently pass with zero cases and this
    whole file would stop meaning anything."""
    assert len(_FILES) >= 40, (
        f"Expected the open-designated scope to contain ~43 files, found {len(_FILES)} — "
        f"OPEN_DESIGNATED_DIRS/OPEN_DESIGNATED_FILES may be out of date."
    )
