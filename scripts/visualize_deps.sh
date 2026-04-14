#!/usr/bin/env bash
# ============================================================
# visualize_deps.sh — Generate visual dependency graphs
# ============================================================
# Requirements:
#   Python: pip install pydeps graphviz
#   Node:   graphviz system package (for `dot` CLI)
#   Run from INTELLIGENCE_ITA/ root directory
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
OUT_DIR="$ROOT_DIR/docs/generated"

echo "=== INTELLIGENCE_ITA Dependency Visualizer ==="
echo "Output directory: $OUT_DIR"
mkdir -p "$OUT_DIR"

# ── Python: pydeps ──────────────────────────────────────────

echo ""
echo "▶ Installing pydeps (if needed)..."
pip install pydeps --quiet 2>/dev/null || {
    echo "  ⚠️  pydeps install failed — skipping Python graphs"
    SKIP_PYTHON=1
}

if [ -z "$SKIP_PYTHON" ]; then
    cd "$ROOT_DIR"
    echo ""
    echo "▶ Python: Full src/ dependency graph..."
    pydeps src \
        --max-bacon=3 \
        --cluster \
        --rankdir TB \
        --noshow \
        -o "$OUT_DIR/python_deps_full.svg" 2>/dev/null && \
        echo "  ✅ $OUT_DIR/python_deps_full.svg" || \
        echo "  ⚠️  Full graph skipped (spaCy model may be missing)"

    echo ""
    echo "▶ Python: LLM module graph..."
    pydeps src/llm \
        --max-bacon=2 \
        --noshow \
        -o "$OUT_DIR/python_deps_llm.svg" 2>/dev/null && \
        echo "  ✅ $OUT_DIR/python_deps_llm.svg" || \
        echo "  ⚠️  LLM graph skipped"

    echo ""
    echo "▶ Python: NLP module graph..."
    pydeps src/nlp \
        --max-bacon=2 \
        --noshow \
        -o "$OUT_DIR/python_deps_nlp.svg" 2>/dev/null && \
        echo "  ✅ $OUT_DIR/python_deps_nlp.svg" || \
        echo "  ⚠️  NLP graph skipped"

    echo ""
    echo "▶ Python: Macro module graph..."
    pydeps src/macro \
        --max-bacon=2 \
        --noshow \
        -o "$OUT_DIR/python_deps_macro.svg" 2>/dev/null && \
        echo "  ✅ $OUT_DIR/python_deps_macro.svg" || \
        echo "  ⚠️  Macro graph skipped"
fi

# ── Python: pyreverse (UML class diagrams) ──────────────────

echo ""
echo "▶ Python UML: LLM class diagram (pyreverse)..."
mkdir -p "$OUT_DIR/uml"
cd "$ROOT_DIR"
pyreverse src/llm \
    -o svg \
    -d "$OUT_DIR/uml/" \
    --ignore=tools \
    2>/dev/null && \
    echo "  ✅ $OUT_DIR/uml/classes_src.svg" || \
    echo "  ⚠️  pyreverse skipped (install via: pip install pylint)"

# ── TypeScript: dependency-cruiser ──────────────────────────

echo ""
echo "▶ TypeScript: dependency-cruiser (frontend)..."
cd "$ROOT_DIR/web-platform"

if ! command -v npx &>/dev/null; then
    echo "  ⚠️  npx not found — skipping TypeScript graph"
else
    # Check if graphviz dot is available
    if ! command -v dot &>/dev/null; then
        echo "  ⚠️  graphviz 'dot' not found — install with: brew install graphviz"
        echo "       Generating JSON output instead..."
        npx depcruise src \
            --include-only "^src" \
            --output-type json \
            2>/dev/null > "$OUT_DIR/frontend_deps.json" && \
            echo "  ✅ $OUT_DIR/frontend_deps.json (JSON — no graphviz)" || \
            echo "  ⚠️  depcruise failed"
    else
        npx depcruise src \
            --include-only "^src" \
            --output-type dot \
            2>/dev/null | dot -T svg > "$OUT_DIR/frontend_deps.svg" && \
            echo "  ✅ $OUT_DIR/frontend_deps.svg" || \
            echo "  ⚠️  depcruise failed (try: npm install -g dependency-cruiser)"

        echo ""
        echo "▶ TypeScript: circular dependency check..."
        npx depcruise src \
            --include-only "^src" \
            --validate \
            2>/dev/null | tee "$OUT_DIR/frontend_violations.txt" && \
            echo "  ✅ $OUT_DIR/frontend_violations.txt" || \
            echo "  ⚠️  violations check skipped"
    fi
fi

# ── pipdeptree: pip dependency tree ─────────────────────────

cd "$ROOT_DIR"
echo ""
echo "▶ pip dependency tree..."
pip install pipdeptree --quiet 2>/dev/null
pipdeptree --warn silence 2>/dev/null > "$OUT_DIR/pip_tree.txt" && \
    echo "  ✅ $OUT_DIR/pip_tree.txt"

pipdeptree \
    --packages openbb,fastapi,sentence-transformers,spacy,psycopg2-binary \
    --warn silence \
    2>/dev/null > "$OUT_DIR/pip_tree_key_packages.txt" && \
    echo "  ✅ $OUT_DIR/pip_tree_key_packages.txt (key packages only)"

# ── Summary ─────────────────────────────────────────────────

echo ""
echo "=== Done. Generated files in $OUT_DIR/ ==="
ls -lh "$OUT_DIR"/*.svg "$OUT_DIR"/*.txt "$OUT_DIR"/*.json 2>/dev/null || true
echo ""
echo "To open SVGs:"
echo "  open $OUT_DIR/python_deps_full.svg"
echo "  open $OUT_DIR/frontend_deps.svg"
