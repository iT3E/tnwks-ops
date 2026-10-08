#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Render every diagram in this directory to SVG.
#
#   ./render.sh            render all
#   ./render.sh mermaid    render one engine (mermaid|d2|plantuml)
#
# Each engine is optional. Missing tools are reported and skipped rather than
# failing the run, so you can work on one language without installing all three.
#
# Install notes, none of which need root:
#   mermaid   npx -y @mermaid-js/mermaid-cli   (downloads a private Chrome)
#   d2        curl -fsSL https://d2lang.com/install.sh | sh -s -- --prefix ~/.local
#   plantuml  curl -fsSL -o ~/.local/lib/plantuml.jar \
#               https://github.com/plantuml/plantuml/releases/download/v1.2026.0/plantuml-1.2026.0.jar
# ---------------------------------------------------------------------------
set -uo pipefail
cd "$(dirname "$0")"

WHICH="${1:-all}"
PLANTUML_JAR="${PLANTUML_JAR:-$HOME/.local/lib/plantuml.jar}"
export PATH="$HOME/.local/bin:$PATH"
rc=0

render_mermaid() {
  echo "== mermaid =="
  if ! command -v npx >/dev/null 2>&1; then
    echo "   SKIP: npx not found"
    return 0
  fi
  # Config lives inline so the render is reproducible without an extra file.
  local cfg
  cfg="$(mktemp)"
  printf '{ "theme": "neutral", "flowchart": { "curve": "basis", "nodeSpacing": 45, "rankSpacing": 55 } }\n' > "$cfg"

  for src in *.mmd; do
    [ -e "$src" ] || continue
    local out="${src%.mmd}-mermaid.svg"
    if npx -y @mermaid-js/mermaid-cli@latest -i "$src" -o "$out" \
         -c "$cfg" -b transparent -w 1800 >/dev/null 2>&1; then
      echo "   OK   $src -> $out"
    else
      echo "   FAIL $src"
      rc=1
    fi
  done
  rm -f "$cfg"
}

render_d2() {
  echo "== d2 =="
  if ! command -v d2 >/dev/null 2>&1; then
    echo "   SKIP: d2 not found"
    return 0
  fi
  for src in *.d2; do
    [ -e "$src" ] || continue
    local out="${src%.d2}-d2.svg"
    if d2 --theme 1 --layout elk "$src" "$out" >/dev/null 2>&1; then
      echo "   OK   $src -> $out"
    else
      echo "   FAIL $src"
      rc=1
    fi
  done
}

render_plantuml() {
  echo "== plantuml =="
  if [ ! -f "$PLANTUML_JAR" ]; then
    echo "   SKIP: no jar at $PLANTUML_JAR"
    return 0
  fi
  if ! command -v java >/dev/null 2>&1; then
    echo "   SKIP: java not found"
    return 0
  fi
  # The .puml files set `!pragma layout smetana` so no system Graphviz is needed.
  # Output name comes from the @startuml id, which is why those ids end in
  # -plantuml: it keeps them from overwriting the mermaid SVG of the same diagram.
  for src in *.puml; do
    [ -e "$src" ] || continue
    if java -jar "$PLANTUML_JAR" -tsvg "$src" >/dev/null 2>&1; then
      echo "   OK   $src"
    else
      echo "   FAIL $src"
      rc=1
    fi
  done
}

case "$WHICH" in
  all)      render_mermaid; render_d2; render_plantuml ;;
  mermaid)  render_mermaid ;;
  d2)       render_d2 ;;
  plantuml) render_plantuml ;;
  *) echo "usage: $0 [all|mermaid|d2|plantuml]"; exit 2 ;;
esac

exit $rc
