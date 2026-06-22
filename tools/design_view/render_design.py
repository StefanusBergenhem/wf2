#!/usr/bin/env python3
"""render_design.py — render an SA design-graph into ONE self-contained HTML page.

The agent authors a small SEMANTIC design graph (components + dependencies + the
moves this change makes + requirement allocation) and pipes it as JSON on stdin; this
script translates it to a styled graph beside a details panel, on the shared graph-view
chassis (offline, spacing, side panel). The output is TRANSIENT: a conversation aid
regenerated as the design evolves, never a committed artifact.

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
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "graphview"))
import chassis  # noqa: E402

# state -> (background, border) for component nodes
NODE_STYLE = {
    "existing": ("#eceff1", "#607d8b"),
    "new":      ("#c8e6c9", "#2e7d32"),
    "split":    ("#bbdefb", "#1565c0"),
    "merged":   ("#e1bee7", "#6a1b9a"),
    "removed":  ("#ffcdd2", "#c62828"),
}
# state -> (color, dashed?) for dependency edges
EDGE_STYLE = {
    "existing": ("#94a3b8", False),
    "added":    ("#2e7d32", False),
    "removed":  ("#c62828", True),
    "changed":  ("#ef6c00", True),
}

SUMMARY = ("The change this design makes — components and their dependencies at the "
           "altitude this change lives. Node colour is its move; a dashed edge is a "
           "dependency removed or changed. Click a node (or “All components”) for its "
           "note and allocated requirements.")

BAR = """<button onclick="showAll()">▦ All components</button>
<span class="legend">
 <span><span class="chip" style="background:#c8e6c9;border-color:#2e7d32">&nbsp;</span> new</span>
 <span><span class="chip" style="background:#eceff1;border-color:#607d8b">&nbsp;</span> existing</span>
 <span>— dashed edge = removed / changed</span>
</span>"""

EXTRA_CSS = """
 table{width:100%;border-collapse:collapse;font-size:12px}
 td{padding:6px;border-bottom:1px solid #f1f5f9;vertical-align:top}
 td.e{white-space:nowrap;font:12px ui-monospace,Menlo,monospace;font-weight:600}
 .rq{color:#2e7d32;font:11px ui-monospace,monospace;margin-top:3px}
 .rel{font:12px ui-monospace,monospace;padding:3px 0;border-bottom:1px solid #f1f5f9}
 .rel .lbl{color:var(--mut)}
"""

# Built once in Python, run in the page: build the styled graph from DATA, then drive
# the side panel — a table of every component (note + allocated reqs) by default, one
# component's detail + its dependencies on click.
BODY_JS = r"""
const NS=DATA.node_palette, ES=DATA.edge_palette, N={};
DATA.nodes.forEach(n=>N[n.id]=n);
const OUT={}, INC={};
DATA.edges.forEach(e=>{(OUT[e.from]=OUT[e.from]||[]).push(e);(INC[e.to]=INC[e.to]||[]).push(e);});
const vnodes=DATA.nodes.map(n=>{const c=NS[n.state]||NS.existing;return{
  id:n.id, label:n.label, shape:"box", borderWidth:2,
  color:{background:c[0], border:c[1]},
  shapeProperties:{borderDashes:n.state=="removed"?[6,4]:false}, font:{size:14}};});
const vedges=DATA.edges.map(e=>{const c=ES[e.state]||ES.existing;return{
  from:e.from, to:e.to, label:e.label, arrows:"to",
  font:{size:11, color:"#64748b", strokeWidth:4},
  color:{color:c[0]}, dashes:c[1], smooth:{type:"continuous"}};});
WF.draw(vnodes, vedges);
const chip=s=>{const c=NS[s]||NS.existing;
  return `<span class="chip" style="background:${c[0]};border-color:${c[1]}">${s}</span>`;};
function showAll(){
  WF.panelHead("All components ("+DATA.nodes.length+")");
  WF.panel("<table>"+DATA.nodes.map(n=>
    `<tr class="row" onclick="showOne('${n.id}')"><td class="e">${n.label}<br>${chip(n.state)}</td>`+
    `<td>${n.note||"<span class=empty>—</span>"}`+
    `${n.reqs.length?`<div class="rq">▸ ${n.reqs.join(", ")}</div>`:""}</td></tr>`).join("")+"</table>");
}
function showOne(id){const n=N[id]; if(!n) return;
  WF.panelHead("Component");
  const rel=(arr,out)=>(arr||[]).map(e=>{const o=out?e.to:e.from;
    const tag=e.state=="removed"?" (removed)":e.state=="added"?" (added)":e.state=="changed"?" (changed)":"";
    return `<div class="rel">${out?"":"← "}<b>${N[o]?N[o].label:o}</b> <span class="lbl">${e.label}${tag}</span></div>`;
  }).join("")||"<div class=empty>none</div>";
  WF.panel(`<div class="nm">${n.label}</div><div style="margin:6px 0">${chip(n.state)}</div>`+
    `<div class="desc">${n.note||"<span class=empty>no note</span>"}</div>`+
    (n.reqs.length?`<div class="grp">Requirements allocated here</div><div class="rq">${n.reqs.join("<br>")}</div>`:"")+
    `<div class="grp">Depends on</div>${rel(OUT[id],true)}`+
    `<div class="grp">Depended on by</div>${rel(INC[id],false)}`);
}
WF.onNode(showOne);
showAll();
"""


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


def build_data(design):
    comps = design["components"]
    deps = design.get("dependencies", []) or []
    allocation = design.get("allocation", []) or []

    reqs = {}
    for a in allocation:
        reqs.setdefault(a.get("component"), []).append(a.get("requirement"))

    nodes = []
    for c in comps:
        cid = c.get("id") or c.get("label")
        if not cid:
            fail("every component needs an 'id' or 'label'")
        nodes.append({
            "id": cid,
            "label": c.get("label", cid),
            "state": c.get("state", "existing"),
            "note": c.get("note", ""),
            "reqs": [r for r in reqs.get(cid, []) if r],
        })

    edges = []
    for e in deps:
        if not e.get("from") or not e.get("to"):
            fail("every dependency needs 'from' and 'to'")
        edges.append({
            "from": e["from"], "to": e["to"],
            "label": e.get("label", ""),
            "state": e.get("state", "existing"),
        })

    return {
        "nodes": nodes, "edges": edges,
        "node_palette": {k: list(v) for k, v in NODE_STYLE.items()},
        "edge_palette": {k: list(v) for k, v in EDGE_STYLE.items()},
    }


def main():
    ap = argparse.ArgumentParser(description="Render an SA design graph to self-contained HTML.")
    ap.add_argument("--out", required=True, help="output HTML path")
    args = ap.parse_args()

    design = load_design()
    data = build_data(design)
    page = chassis.render_page(
        title=design.get("title", "design"),
        summary=SUMMARY,
        bar_html=BAR,
        panel_html="",
        body_js=BODY_JS,
        data=data,
        extra_css=EXTRA_CSS,
    )

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print(f"render_design: wrote {args.out} "
          f"({len(data['nodes'])} components, {len(data['edges'])} deps)")


if __name__ == "__main__":
    main()
