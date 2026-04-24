# draw.io (.drawio) Editing Guidelines

## Common Mistakes to Avoid

### 1. HTML rendering in edge labels
- **Problem**: Edge labels with HTML tags (e.g. `<font>`, `<b>`) render as raw text instead of formatted text.
- **Fix**: Always include `html=1;` in the `style` attribute of any `mxCell` that uses HTML in its `value`.
- Applies to: edges, nodes, text labels — anything with HTML markup in `value`.

### 2. HTML rendering in node values
- Nodes that use `&lt;b&gt;`, `&lt;font&gt;`, `&lt;br&gt;` etc. in their `value` attribute **must** have `html=1;` in their `style`.
- Without it, tags display literally instead of being interpreted.

## Style Checklist for mxCell Elements

| Element type | Required style properties |
|---|---|
| Node with HTML value | `html=1;` |
| Edge with HTML label | `html=1;` |
| Plain text label | `html=1;` not strictly needed but harmless |

## Conventions Used in This Project

- **🔧** = LLM tool-calling enabled (agent can invoke external tools)
- **⚡** = mini LLM call (lighter/cheaper model, used by specialist agents)
- **🧠** = primary LLM call (heavier model, used by consolidator)
- Node colors:
  - `#dae8fc` (blue) = validation node
  - `#d5e8d4` (green) = specialist agents (parallel)
  - `#e1d5e7` (purple) = consolidator (fan-in)
  - `#fff2cc` (yellow diamond) = conditional routing
  - `#f8cecc` (red ellipse) = END nodes
  - `#ffffcc` (yellow note) = annotations / state schema
  - `#f5f5f5` (grey) = legend background
- Edge colors:
  - `#006600` (green) = fan-out to specialists
  - `#666666` (grey) = fan-in to consolidator
  - `#cc0000` (red, dashed) = error path
  - `#333333` (dark) = default flow

## Geometry Tips

- When adding content lines to a node (e.g. adding an LLM indicator), increase the node height (~10px per line).
- After resizing upstream nodes, adjust downstream node positions (y-coordinates) so edges don't overlap.
- Legend box height must accommodate all legend entries.
