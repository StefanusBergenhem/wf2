#!/usr/bin/env python3
"""
render_design.py — render an SA design-graph into ONE self-contained HTML page.

The agent authors a small SEMANTIC design graph (components + dependencies + the
moves this change makes + AC allocation) and pipes it as JSON on stdin; this script
does the mechanical work — translate it to a styled vis-network graph and inline the
vendored lib so the page opens offline in any browser. The output is TRANSIENT: a
conversation aid regenerated as the design evolves, never a committed artifact.

Input JSON (stdin):
  {
    "title": "<change summary>",                         # optional
    "components": [                                       # required, >=1
      {"id": "auth", "label": "auth", "state": "existing", "note": "..."}
    ],
    "dependencies": [                                     # optional
      {"from": "gateway", "to": "auth", "state": "existing", "label": "..."}
    ],
    "allocation": [                                       # optional
      {"requirement": "REQ-2", "component": "auth"}
    ]
  }
  component.state ∈ existing | new | split | merged | removed
  dependency.state ∈ existing | added | removed | changed

Usage:  ... | render_design.py --out design-view.html
"""
import argparse
import html
import json
import os
import sys

# state -> (background, border, dashed?) for component nodes
NODE_STYLE = {
    "existing": ("#eceff1", "#607d8b", False),
    "new":      ("#c8e6c9", "#2e7d32", False),
    "split":    ("#bbdefb", "#1565c0", False),
    "merged":   ("#e1bee7", "#6a1b9a", False),
    "removed":  ("#ffcdd2", "#c62828", True),
}
# state -> (color, dashed?) for dependency edges
EDGE_STYLE = {
    "existing": ("#90a4ae", False),
    "added":    ("#2e7d32", False),
    "removed":  ("#c62828", True),
    "changed":  ("#ef6c00", True),
}


def fail(msg):
    sys.stderr.write(f"render_design: {msg}\n")
    sys.exit(1)


def load_design():
    raw = sys.stdin.read()
    if not raw.strip():
        fail("no design JSON on stdin")
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"invalid JSON on stdin: {e}")
    if not isinstance(d, dict):
        fail("design must be a JSON object")
    comps = d.get("components")
    if not isinstance(comps, list) or not comps:
        fail("design needs a non-empty 'components' list")
    return d


def build_nodes(comps, allocation):
    # group allocated requirements per component for the tooltip
    reqs = {}
    for a in allocation:
        reqs.setdefault(a.get("component"), []).append(a.get("requirement"))
    nodes = []
    for c in comps:
        cid = c.get("id") or c.get("label")
        if not cid:
            fail("every component needs an 'id' or 'label'")
        state = c.get("state", "existing")
        bg, border, dashed = NODE_STYLE.get(state, NODE_STYLE["existing"])
        tip = [f"state: {state}"]
        if c.get("note"):
            tip.append(c["note"])
        if reqs.get(cid):
            tip.append("reqs: " + ", ".join(x for x in reqs[cid] if x))
        nodes.append({
            "id": cid,
            "label": c.get("label", cid),
            "title": " | ".join(tip),
            "shape": "box",
            "borderWidth": 2,
            "color": {"background": bg, "border": border},
            "shapeProperties": {"borderDashes": [6, 4] if dashed else False},
        })
    return nodes


def build_edges(deps):
    edges = []
    for e in deps:
        if not e.get("from") or not e.get("to"):
            fail("every dependency needs 'from' and 'to'")
        state = e.get("state", "existing")
        color, dashed = EDGE_STYLE.get(state, EDGE_STYLE["existing"])
        edges.append({
            "from": e["from"], "to": e["to"],
            "label": e.get("label", ""),
            "arrows": "to",
            "color": {"color": color},
            "dashes": dashed,
        })
    return edges


def lib_block():
    """Inline the vendored vis-network for an offline page; fall back to CDN."""
    vendored = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "vendor", "vis-network.min.js")
    if os.path.exists(vendored):
        with open(vendored, encoding="utf-8") as fh:
            return ("<!-- vis-network inlined (offline) -->\n<script>\n"
                    + fh.read() + "\n</script>")
    return ('<script src="https://unpkg.com/vis-network@9.1.9/'
            'standalone/umd/vis-network.min.js"></script>')


LEGEND = """
<div id="legend">
  <b>components</b>
  <span class="sw existing-component">existing</span>
  <span class="sw new-component">new</span>
  <span class="sw split-component">split</span>
  <span class="sw merged-component">merged</span>
  <span class="sw removed-component">removed</span>
  &nbsp;&nbsp;<b>deps</b>
  <span class="sw existing-dep">existing</span>
  <span class="sw added-dep">added</span>
  <span class="sw changed-dep">changed</span>
  <span class="sw removed-dep">removed</span>
</div>
"""


def render(design):
    title = design.get("title", "design")
    nodes = build_nodes(design["components"], design.get("allocation", []) or [])
    edges = build_edges(design.get("dependencies", []) or [])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)} — design view</title>
<style>
  body{{font-family:system-ui,sans-serif;margin:0}}
  #hd{{padding:8px 12px;background:#263238;color:#fff;font-size:15px}}
  #legend{{padding:6px 12px;font-size:12px;color:#444;border-bottom:1px solid #ddd}}
  .sw{{display:inline-block;padding:1px 7px;margin:0 3px;border-radius:3px;border:2px solid}}
  .existing-component{{background:#eceff1;border-color:#607d8b}}
  .new-component{{background:#c8e6c9;border-color:#2e7d32}}
  .split-component{{background:#bbdefb;border-color:#1565c0}}
  .merged-component{{background:#e1bee7;border-color:#6a1b9a}}
  .removed-component{{background:#ffcdd2;border-color:#c62828;border-style:dashed}}
  .existing-dep{{border-color:#90a4ae}} .added-dep{{border-color:#2e7d32}}
  .changed-dep{{border-color:#ef6c00;border-style:dashed}}
  .removed-dep{{border-color:#c62828;border-style:dashed}}
  #net{{width:100vw;height:calc(100vh - 64px)}}
</style></head>
<body>
<div id="hd">{html.escape(title)} — <i>design view (transient)</i></div>
{LEGEND}
<div id="net"></div>
{lib_block()}
<script>
  const nodes = new vis.DataSet({json.dumps(nodes)});
  const edges = new vis.DataSet({json.dumps(edges)});
  new vis.Network(document.getElementById("net"), {{nodes, edges}}, {{
    physics: {{stabilization: true}},
    layout: {{improvedLayout: true}},
    edges: {{smooth: {{type: "cubicBezier"}}}}
  }});
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Render an SA design graph to self-contained HTML.")
    ap.add_argument("--out", required=True, help="output HTML path")
    args = ap.parse_args()
    design = load_design()
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render(design))
    print(f"render_design: wrote {args.out} "
          f"({len(design['components'])} components, "
          f"{len(design.get('dependencies', []) or [])} deps)")


if __name__ == "__main__":
    main()
