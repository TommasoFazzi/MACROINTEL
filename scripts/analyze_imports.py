#!/usr/bin/env python3
"""
analyze_imports.py — Static AST import analyzer for INTELLIGENCE_ITA
Generates Mermaid dependency diagrams without importing any packages.
Uses only Python stdlib (ast, pathlib, collections).

Usage:
    python scripts/analyze_imports.py
    python scripts/analyze_imports.py --output docs/generated/imports.md
    python scripts/analyze_imports.py --module src/llm --depth 2
"""

import ast
import sys
import argparse
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src"

# Top-level src/ packages to track as internal modules
INTERNAL_PACKAGES = {
    d.name for d in SRC.iterdir()
    if d.is_dir() and (d / "__init__.py").exists()
}

# External packages worth showing (filter noise)
NOTABLE_EXTERNAL = {
    "fastapi", "pydantic", "google.generativeai", "spacy",
    "sentence_transformers", "sklearn", "hdbscan", "psycopg2",
    "pgvector", "openbb", "yfinance", "aiohttp", "trafilatura",
    "streamlit", "feedparser", "numpy", "pandas",
}


def parse_imports(filepath: Path) -> tuple[set[str], set[str]]:
    """Return (internal_imports, external_imports) for a .py file."""
    internal, external = set(), set()
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return internal, external

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top == "src":
                    pkg = alias.name.split(".")[1] if len(alias.name.split(".")) > 1 else None
                    if pkg:
                        internal.add(pkg)
                elif top in INTERNAL_PACKAGES:
                    internal.add(top)
                elif top in NOTABLE_EXTERNAL:
                    external.add(top)

        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            parts = node.module.split(".")
            top = parts[0]
            if top == "src":
                pkg = parts[1] if len(parts) > 1 else None
                if pkg:
                    internal.add(pkg)
            elif top in INTERNAL_PACKAGES:
                internal.add(top)
            elif top in NOTABLE_EXTERNAL:
                external.add(top)

    return internal, external


def analyze_package(package_dir: Path) -> dict:
    """Analyze all .py files in a package directory."""
    result = {
        "internal": defaultdict(set),   # module -> set of internal deps
        "external": defaultdict(set),   # module -> set of external deps
        "files": [],
    }
    for pyfile in sorted(package_dir.rglob("*.py")):
        if pyfile.name.startswith("test_"):
            continue
        rel = pyfile.relative_to(SRC)
        module_parts = list(rel.parts)
        module_parts[-1] = module_parts[-1].replace(".py", "")
        module = "/".join(module_parts)

        internal, external = parse_imports(pyfile)
        # Remove self-references
        pkg_name = package_dir.name
        internal.discard(pkg_name)

        if internal or external:
            result["internal"][module] = internal
            result["external"][module] = external
        result["files"].append(pyfile.name)

    return result


def build_package_level_deps() -> dict[str, set[str]]:
    """Build package-level dependency map (src/X → src/Y)."""
    pkg_deps: dict[str, set[str]] = defaultdict(set)
    for pkg_dir in sorted(SRC.iterdir()):
        if not pkg_dir.is_dir() or not (pkg_dir / "__init__.py").exists():
            continue
        pkg = pkg_dir.name
        for pyfile in pkg_dir.rglob("*.py"):
            internal, _ = parse_imports(pyfile)
            for dep in internal:
                if dep != pkg and dep in INTERNAL_PACKAGES:
                    pkg_deps[pkg].add(dep)
    return dict(pkg_deps)


def mermaid_package_graph(pkg_deps: dict[str, set[str]]) -> str:
    """Generate Mermaid flowchart for package-level deps."""
    lines = ["```mermaid", "flowchart LR"]

    # Node labels with descriptions
    labels = {
        "ingestion": "src/ingestion\\nIngestionPipeline\\nFeedParser",
        "nlp":       "src/nlp\\nNLPProcessor\\nNarrativeProcessor",
        "llm":       "src/llm\\nOracleOrchestrator\\nReportGenerator",
        "macro":     "src/macro\\nConvergence\\nRegimePersistence",
        "storage":   "src/storage\\nDatabaseManager",
        "knowledge": "src/knowledge\\nOntologyManager",
        "integrations": "src/integrations\\nOpenBBMarketService",
        "api":       "src/api\\nFastAPI routers",
        "finance":   "src/finance\\nSignal scoring",
        "hitl":      "src/hitl\\nStreamlit dashboard",
        "services":  "src/services\\nReportCompare\\nTickerService",
    }

    styles = {
        "ingestion": "fill:#1a3a5c,color:#fff",
        "nlp":       "fill:#1a5c3a,color:#fff",
        "llm":       "fill:#5c1a1a,color:#fff",
        "macro":     "fill:#5c3a1a,color:#fff",
        "api":       "fill:#3a1a5c,color:#fff",
        "storage":   "fill:#2a4a6a,color:#fff",
        "knowledge": "fill:#5c5c1a,color:#fff",
        "integrations": "fill:#1a5c5c,color:#fff",
    }

    all_pkgs = set(pkg_deps.keys()) | {d for deps in pkg_deps.values() for d in deps}

    for pkg in sorted(all_pkgs):
        label = labels.get(pkg, f"src/{pkg}")
        lines.append(f'    {pkg}["{label}"]')

    lines.append("")
    for pkg, deps in sorted(pkg_deps.items()):
        for dep in sorted(deps):
            lines.append(f"    {pkg} --> {dep}")

    lines.append("")
    for pkg, style in styles.items():
        if pkg in all_pkgs:
            lines.append(f"    style {pkg} {style}")

    lines.append("```")
    return "\n".join(lines)


def mermaid_module_graph(pkg_name: str, result: dict) -> str:
    """Generate Mermaid flowchart for file-level deps within a package."""
    lines = ["```mermaid", "flowchart TD"]

    all_modules = set(result["internal"].keys()) | set(result["external"].keys())
    if not all_modules:
        return ""

    # Internal nodes
    for mod in sorted(all_modules):
        short = mod.split("/")[-1]
        lines.append(f'    {short}["{short}.py"]')

    # External nodes (grouped)
    ext_all: set[str] = set()
    for exts in result["external"].values():
        ext_all.update(exts)
    for ext in sorted(ext_all):
        safe = ext.replace(".", "_")
        lines.append(f'    EXT_{safe}(("{ext}"))')

    lines.append("")

    # Edges: internal → internal
    for mod, deps in sorted(result["internal"].items()):
        src_short = mod.split("/")[-1]
        for dep in sorted(deps):
            # dep is a package name like "storage" → point to package label
            lines.append(f"    {src_short} --> {dep}")

    # Edges: internal → external
    for mod, exts in sorted(result["external"].items()):
        src_short = mod.split("/")[-1]
        for ext in sorted(exts):
            safe = ext.replace(".", "_")
            lines.append(f"    {src_short} --> EXT_{safe}")

    lines.append("```")
    return "\n".join(lines)


def mermaid_external_deps(pkg_deps: dict[str, set[str]]) -> str:
    """Show which packages use which external libraries."""
    ext_by_pkg: dict[str, set[str]] = defaultdict(set)
    for pkg_dir in sorted(SRC.iterdir()):
        if not pkg_dir.is_dir() or not (pkg_dir / "__init__.py").exists():
            continue
        pkg = pkg_dir.name
        for pyfile in pkg_dir.rglob("*.py"):
            _, external = parse_imports(pyfile)
            ext_by_pkg[pkg].update(external)

    lines = ["```mermaid", "flowchart LR"]
    ext_all: set[str] = set()
    for exts in ext_by_pkg.values():
        ext_all.update(exts)

    for ext in sorted(ext_all):
        safe = ext.replace(".", "_")
        lines.append(f'    EXT_{safe}(("{ext}"))')

    lines.append("")
    for pkg, exts in sorted(ext_by_pkg.items()):
        for ext in sorted(exts):
            safe = ext.replace(".", "_")
            lines.append(f"    {pkg} --> EXT_{safe}")

    lines.append("```")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Static import analyzer — generates Mermaid diagrams")
    parser.add_argument("--output", default="docs/generated/imports.md",
                        help="Output markdown file (default: docs/generated/imports.md)")
    parser.add_argument("--module", default=None,
                        help="Analyze a specific module (e.g. src/llm)")
    args = parser.parse_args()

    out_path = ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sections = []

    if args.module:
        # Single module analysis
        pkg_dir = ROOT / args.module
        pkg_name = pkg_dir.name
        print(f"▶ Analyzing {args.module}...")
        result = analyze_package(pkg_dir)
        sections.append(f"# Import Analysis — {args.module}\n")
        sections.append(f"## File-level dependency graph\n")
        graph = mermaid_module_graph(pkg_name, result)
        if graph:
            sections.append(graph)
        else:
            sections.append("_No notable imports found._")

        sections.append("\n## Import details\n")
        for mod in sorted(result["internal"].keys() | result["external"].keys()):
            short = mod.split("/")[-1]
            internal = sorted(result["internal"].get(mod, set()))
            external = sorted(result["external"].get(mod, set()))
            if internal or external:
                sections.append(f"**{short}.py**")
                if internal:
                    sections.append(f"  - Internal: {', '.join(f'`src/{d}`' for d in internal)}")
                if external:
                    sections.append(f"  - External: {', '.join(f'`{e}`' for e in external)}")
                sections.append("")
    else:
        # Full codebase analysis
        print("▶ Analyzing src/ package structure...")
        pkg_deps = build_package_level_deps()

        print(f"  Found packages: {', '.join(sorted(pkg_deps.keys()))}")

        sections.append("# INTELLIGENCE_ITA — Static Import Analysis\n")
        sections.append("> Generated by `scripts/analyze_imports.py` using AST static analysis.\n")
        sections.append("## Package-level dependency graph\n")
        sections.append(mermaid_package_graph(pkg_deps))

        sections.append("\n## External library usage per package\n")
        sections.append(mermaid_external_deps(pkg_deps))

        sections.append("\n## Dependency details\n")
        for pkg, deps in sorted(pkg_deps.items()):
            sections.append(f"**src/{pkg}** → {', '.join(f'`src/{d}`' for d in sorted(deps)) or '_no internal deps_'}")

        # Per-module detail for key packages
        for pkg_name in ["llm", "nlp", "macro", "api"]:
            pkg_dir = SRC / pkg_name
            if not pkg_dir.exists():
                continue
            print(f"▶ Analyzing src/{pkg_name}...")
            result = analyze_package(pkg_dir)
            sections.append(f"\n## src/{pkg_name} — file-level graph\n")
            graph = mermaid_module_graph(pkg_name, result)
            if graph:
                sections.append(graph)

    output = "\n".join(sections)
    out_path.write_text(output, encoding="utf-8")

    print(f"\n✅ Output: {out_path}")
    print(f"   Open on GitHub or paste into https://mermaid.live")


if __name__ == "__main__":
    main()
