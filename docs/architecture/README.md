# Architecture diagrams

Diagrams-as-code for the tnwks homelab. Source files are the truth, SVGs are
build output, and `./render.sh` regenerates everything.

Everything in `docs/diagrams/` is draw.io XML: fine to look at, impossible to
review in a pull request. These are plain text, so a topology change shows up as
a readable diff.

## What is here

| Diagram | Language | Source | Output |
| --- | --- | --- | --- |
| Current state (two routers) | Mermaid | `current-state.mmd` | `current-state-mermaid.svg` |
| Target state (one RB5009) | Mermaid | `target-state.mmd` | `target-state-mermaid.svg` |
| Target state (one RB5009) | D2 | `target-state.d2` | `target-state-d2.svg` |
| Target state (one RB5009) | PlantUML | `target-state.puml` | `target-state-plantuml.svg` |

The target state is drawn three times **on purpose**. Same topology, same facts,
three languages, so the comparison is about the tool rather than about three
different diagrams. Pick a winner and delete the other two.

## Rendering

```bash
./render.sh              # everything
./render.sh mermaid      # one engine
```

Missing tools are skipped with a note instead of failing the run. No engine needs
root:

```bash
# mermaid  (downloads its own Chrome under ~/.cache/puppeteer)
npx -y @mermaid-js/mermaid-cli --version

# d2
curl -fsSL https://d2lang.com/install.sh | sh -s -- --prefix ~/.local

# plantuml  (needs a JRE, already present)
curl -fsSL -o ~/.local/lib/plantuml.jar \
  https://github.com/plantuml/plantuml/releases/download/v1.2026.0/plantuml-1.2026.0.jar
```

## Verdict after actually drawing the same thing three times

**D2 is the best fit for this repo.** Use it for infrastructure topology.

It was the only one where nesting is structural rather than cosmetic. `rb.input.wg_in`
is a real path, so a WireGuard arrow can terminate on a specific rule inside the
input chain inside the router. Mermaid subgraphs cannot do that cleanly. The
`sql_table` shape also turned out to be the right way to render the VLAN table and
the "native services replaces N containers" mapping, which are genuinely tabular
and look bad as a pile of boxes. Connection styling is per-edge and readable.
Renders in ~15ms from a single 20MB binary with no browser and no JVM.

Cost: another binary to install, and the ELK layout engine puts things where it
wants. Hand-tuning is limited.

**Mermaid is the right default for anything that lives in a Markdown file.** It is
the only one of the three that GitHub renders inline in a `mermaid` fence, so a
diagram in a PR description or a README needs zero build step. Good enough layout
for flows, and the syntax is the least surprising. Weaknesses showed up as soon as
the diagram got real: `subgraph` is a visual hint, not a container, so cross-boundary
edges get messy; no table primitive; styling is a `classDef` block bolted on at the
end rather than per-node.

Two parser gotchas worth knowing, both cost me time:

- **A bare `%%` line is a syntax error.** `%% text` is a fine comment and `%%` alone
  blows up with `Expecting 'NEWLINE', 'SPACE', 'GRAPH', got 'NODE_STRING'` reported
  against line 1, which is not where the problem is. Every comment line here has
  content after the `%%` for that reason.
- Mermaid emits `width="100%"` with the real size only in `viewBox`, so anything
  post-processing the SVG has to read the viewBox.

**PlantUML is the weakest fit here, but it has one real edge.** Its layout came out
noticeably more cramped than the other two on identical content, and it is the only
engine that needs a JVM. Two traps: it wants a system Graphviz for anything that is
not a sequence diagram (fixed here with `!pragma layout smetana`, its bundled
pure-Java dot port), and the output filename comes from the `@startuml` id, so
`@startuml target-state` silently overwrote the Mermaid `target-state.svg`. Hence
the `-plantuml` suffix on the id.

Where it wins: `node`, `component`, `artifact` and `actor` are semantic deployment
elements, not generic rectangles. For "what physically runs where" it carries
meaning the other two only imply with color. If a deployment or sequence diagram is
ever needed, reach for this.

### Also looked at, not used

- **Structurizr DSL** — C4 model, one model rendered as several views. Genuinely
  the right tool for layered software architecture, overkill for one router.
- **Graphviz DOT** — the substrate under half of these. Too low-level to hand-write.
- **Pikchr, Excalidraw** — Pikchr is niche outside Fossil; Excalidraw is a
  whiteboard, not a reviewable artifact.

## Ground rules

- Edit the source, never the SVG.
- Every fact should be traceable to Terraform, the VyOS config repo, or a discovery
  doc. Both diagrams cite sources in a header comment.
- Colors carry meaning, they are not decoration: green is new or native, blue is
  routing, red is broken or dangerous, amber needs a decision, grey dashed is dead.

## Sources

- `docs/edgerouter-discovery.md` — live ERL discovery, 2026-09-19
- `docs/mikrotik-vyos-port.md` — port mapping and cutover runbook
- `infrastructure/terraform/modules/mikrotik/` and the generated
  `environments/prod/mikrotik/locals.tf`
- `iT3E/vyos-config` `config-parts/*.sh`
- Obsidian `docs/network/Network Current State.md` — live SSH audit, Aruba port map
