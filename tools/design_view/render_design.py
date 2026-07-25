#!/usr/bin/env python3
"""render_design.py — render an SA design-graph into ONE self-contained HTML page.

The agent authors a small SEMANTIC graph and pipes it as JSON on stdin; this script
translates it to a styled graph beside a details panel, on the shared graph-view
chassis (offline, spacing, side panel). The output is TRANSIENT: a conversation aid
regenerated as the design evolves, never a committed artifact.

Author only the CHANGE. Everything the toolchain already knows is derived, not retyped:
each component's prose description from discover's model + subsystem map, and the
system test cases already proven, harvested from the [SYS-TC:] proving-test tags
(reconcile's harvester). The view then shows the slice IN CONTEXT — new work against
what the system already provably promises end-to-end.

Those three inputs are MECHANICAL — discover writes its output to config-declared paths and
the test roots are config-declared — so they default from `.wf/config.yaml`
(paths.discover_model / paths.discover_subsystems / paths.tests) and no caller has to pass
them. The flags exist for standalone use and override the config; only an explicitly passed
path is an intent worth failing over, so a missing config-declared one warns and degrades.
Name components by the id the brief lists (the model's `id`), which is what resolution keys on.

Two input shapes, detected from the JSON's top-level keys:

Design mode — components + dependencies + the moves this change makes + allocation:
  {
    "title": "<change summary>",                         # optional
    "components": [                                       # required, >=1
      {"id": "auth", "label": "auth", "state": "existing", "note": "..."}
    ],
    "dependencies": [                                     # optional
      {"from": "gateway", "to": "auth", "state": "existing", "label": "..."}
    ],
    "allocation": [                                       # optional
      {"requirement": "REQ-2", "component": "auth", "statement": "..."}
    ],
    "system_tests": [                                     # optional — the slice's new ones
      {"id": "SYS-TC-9", "title": "...", "covers": "CAP-3",
       "given": "...", "when": "...", "then": "...", "components": ["auth"]}
    ],
    "decisions": [                                        # optional — what alignment settles
      {"id": "D-1", "title": "...", "question": "...",
       "options": [{"label": "...", "pros": "...", "cons": "..."}],
       "recommended": "...", "status": "open|ratified", "components": ["auth"]}
    ]
  }
  component.state ∈ existing | new | split | merged | removed
  dependency.state ∈ existing | added | removed | changed

  component.note is what THIS CHANGE does to the component; its standing description is
  derived from --descriptions/--model and shown alongside. A component this change
  introduces has no derived description, so its note carries it.

Entity mode — the domain model: entities + labelled relationships:
  {
    "title": "<model summary>",                           # optional
    "entities": [                                          # required, >=1
      {"id": "Room", "label": "Room",
       "attributes": ["name: string"], "note": "..."}      # attributes/note optional
    ],
    "relations": [                                         # optional; label REQUIRED
      {"from": "Room", "to": "Boundary",
       "label": "derived from", "cardinality": "1..*"}     # cardinality optional
    ]
  }

Usage:
  ... | render_design.py --out <view>.html              # inputs resolve from .wf/config.yaml
  ... | render_design.py --out <view>.html [--config <config.yaml>]
        [--model <model.json>] [--descriptions <subsystems.json>]
        [--tests <root> ...] [--test-glob <glob> ...]
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "graphview"))
sys.path.insert(0, os.path.join(_HERE, "..", "reconcile"))
import chassis  # noqa: E402
from reconcile import DEFAULT_TEST_GLOBS, harvest  # noqa: E402

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

SUMMARY = ("This design slice in context — the components it touches, what each already "
           "promises, and what this change adds. Node colour is its move; a dashed edge is "
           "a dependency removed or changed. Click a node for its description, its shipped "
           "requirements and its new ones; “System tests” for the end-to-end behaviours; "
           "“Decisions” for what alignment must settle.")

BAR = """<button onclick="showAll()">▦ Components</button>
<button onclick="showTests()">✓ System tests</button>
<button onclick="showDecisions()">◈ Decisions</button>
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
 .req{padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:12px;line-height:1.45}
 .req .rid{font:11px ui-monospace,monospace;font-weight:600;color:#2e7d32}
 .req.old .rid{color:var(--mut)}
 .req .st{display:block;margin-top:2px}
 /* Proving-test paths are long and appear in both req rows and test cards — never scope
    this to one of them, or the other overflows the panel. */
 .src{color:#94a3b8;font:10px ui-monospace,monospace;margin-top:3px;word-break:break-all}
 .meta{color:var(--mut);font:11px ui-monospace,monospace;margin:4px 0 0}
 /* The overview is for scanning: clamp the description, full text on click-through. */
 td .dsc{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
 .tc{border:1px solid var(--line);border-radius:7px;padding:9px 11px;margin-bottom:9px}
 .tc h3{margin:0 0 5px;font-size:12.5px}
 .tc .gw{font-size:12px;margin:2px 0;line-height:1.45}
 .tc .gw b{color:var(--mut);font-weight:600;display:inline-block;min-width:42px}
 .tag{display:inline-block;font:10px ui-monospace,monospace;padding:1px 6px;border-radius:4px;
      background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;margin-right:5px}
 .tag.ok{background:#f0fdf4;color:#15803d;border-color:#bbf7d0}
 .tag.open{background:#fff7ed;color:#c2410c;border-color:#fed7aa}
 .dc{border:1px solid var(--line);border-radius:7px;padding:9px 11px;margin-bottom:9px;cursor:pointer}
 .dc:hover{border-color:var(--go)}
 .dc h3{margin:4px 0 5px;font-size:12.5px}
 .dc .q{font-size:12px;color:var(--mut);margin-bottom:7px}
 .opt{font-size:12px;padding:5px 0;border-top:1px solid #f1f5f9;line-height:1.45}
 .opt .ol{font-weight:600}
 .opt .pro{color:#15803d} .opt .con{color:#b91c1c}
 .rec{font-size:12px;margin-top:7px;padding-top:6px;border-top:1px solid var(--line)}
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
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// Pull the eye to the components a decision or a test actually moves.
function highlight(ids){const net=WF.net(); if(!net) return;
  const known=(ids||[]).filter(i=>N[i]);
  if(!known.length) return;
  net.selectNodes(known);
  try{net.fit({nodes:known, animation:{duration:400}});}catch(e){}
}
// The call sits in a single-quoted onclick attribute, so an id carrying a quote would
// break out of it; escape both quote forms.
const hl=ids=>`highlight(${JSON.stringify(ids||[]).replace(/'/g,"&#39;").replace(/"/g,"&quot;")})`;
const SCAN=DATA.tags_scanned;
const unscanned='<div class=empty>not scanned — set paths.tests to list what is already proven</div>';
const reqLine=r=>`<div class="req"><span class="rid">${esc(r.id)}</span>`+
  `<span class="st">${esc(r.statement)||"<span class=empty>no statement carried</span>"}</span></div>`;

function showAll(){
  WF.panelHead("Components ("+DATA.nodes.length+")");
  const rows=DATA.nodes.map(n=>
    `<tr class="row" onclick="showOne('${n.id}')"><td class="e">${esc(n.label)}<br>${chip(n.state)}</td>`+
    `<td><div class="dsc">${esc(n.desc)||esc(n.note)||"<span class=empty>—</span>"}</div>`+
    `${n.reqs.length?`<div class="rq">▸ new: ${n.reqs.map(r=>esc(r.id)).join(", ")}</div>`:""}`+
    `</td></tr>`).join("");
  WF.panel("<table>"+rows+"</table>");
}
function showOne(id){const n=N[id]; if(!n) return;
  WF.panelHead("Component");
  const rel=(arr,out)=>(arr||[]).map(e=>{const o=out?e.to:e.from;
    const tag=e.state=="removed"?" (removed)":e.state=="added"?" (added)":e.state=="changed"?" (changed)":"";
    return `<div class="rel">${out?"":"← "}<b>${N[o]?esc(N[o].label):esc(o)}</b> <span class="lbl">${esc(e.label)}${tag}</span></div>`;
  }).join("")||"<div class=empty>none</div>";
  const meta=[n.path&&esc(n.path), n.lang&&esc(n.lang), n.loc&&(n.loc+" loc")].filter(Boolean).join(" · ");
  WF.panel(`<div class="nm">${esc(n.label)}</div><div style="margin:6px 0">${chip(n.state)}</div>`+
    (meta?`<div class="meta">${meta}</div>`:"")+
    `<div class="desc">${esc(n.desc)||esc(n.note)||"<span class=empty>no description</span>"}</div>`+
    (n.desc&&n.note?`<div class="grp">This change</div><div class="desc">${esc(n.note)}</div>`:"")+
    `<div class="grp">New in this slice (${n.reqs.length})</div>`+
    (n.reqs.length?n.reqs.map(reqLine).join(""):"<div class=empty>none</div>")+
    `<div class="grp">Depends on</div>${rel(OUT[id],true)}`+
    `<div class="grp">Depended on by</div>${rel(INC[id],false)}`);
}
function showTests(){
  WF.panelHead("System tests"+(SCAN?" ("+(DATA.sys_new.length+DATA.sys_shipped.length)+")":""));
  const card=t=>`<div class="tc" onclick='${hl(t.components)}'>`+
    `<span class="tag">${esc(t.id)}</span>${t.covers?`<span class="tag ok">covers ${esc(t.covers)}</span>`:""}`+
    `<h3>${esc(t.title)}</h3>`+
    `<div class="gw"><b>Given</b> ${esc(t.given)}</div>`+
    `<div class="gw"><b>When</b> ${esc(t.when)}</div>`+
    `<div class="gw"><b>Then</b> ${esc(t.then)}</div></div>`;
  const old=t=>`<div class="tc"><span class="tag ok">${esc(t.id)}</span>`+
    `<div class="gw" style="margin-top:5px">${esc(t.statement)||"<span class=empty>no description carried</span>"}</div>`+
    `${t.files&&t.files.length?`<div class="src">${t.files.map(esc).join("<br>")}</div>`:""}</div>`;
  WF.panel(`<div class="grp">New in this slice (${DATA.sys_new.length})</div>`+
    (DATA.sys_new.length?DATA.sys_new.map(card).join(""):"<div class=empty>none</div>")+
    `<div class="grp">Already proven ${SCAN?`(${DATA.sys_shipped.length})`:""}</div>`+
    (!SCAN?unscanned:
      DATA.sys_shipped.length?DATA.sys_shipped.map(old).join(""):"<div class=empty>none</div>"));
}
function showDecisions(){
  WF.panelHead("Decisions ("+DATA.decisions.length+")");
  const card=d=>`<div class="dc" onclick='${hl(d.components)}'>`+
    `${d.id?`<span class="tag">${esc(d.id)}</span>`:""}`+
    `<span class="tag ${d.status=="ratified"?"ok":"open"}">${esc(d.status)}</span>`+
    `<h3>${esc(d.title)}</h3>`+
    `${d.question?`<div class="q">${esc(d.question)}</div>`:""}`+
    d.options.map(o=>`<div class="opt"><span class="ol">${esc(o.label)}</span>`+
      `${o.pros?`<div class="pro">+ ${esc(o.pros)}</div>`:""}`+
      `${o.cons?`<div class="con">− ${esc(o.cons)}</div>`:""}</div>`).join("")+
    `${d.recommended?`<div class="rec"><b>Recommended:</b> ${esc(d.recommended)}</div>`:""}</div>`;
  WF.panel(DATA.decisions.length?DATA.decisions.map(card).join("")
    :"<div class=empty>no decisions recorded</div>");
}
WF.onNode(showOne);
showAll();
"""


# --- entity mode: the domain model (labelled ER edges over the domain objects) ---

ER_SUMMARY = ("The domain model — entities and the labelled relationships between "
              "them. An edge label names the relationship; [n..m] is its cardinality. "
              "Click an entity (or “All entities”) for its attributes and relations.")

ER_BAR = """<button onclick="showAll()">▦ All entities</button>
<span class="legend"><span>edge = <i>relationship label</i> · [n..m] = cardinality</span></span>"""

ER_BODY_JS = r"""
const N={}; DATA.nodes.forEach(n=>N[n.id]=n);
const OUT={}, INC={};
DATA.edges.forEach(e=>{(OUT[e.from]=OUT[e.from]||[]).push(e);(INC[e.to]=INC[e.to]||[]).push(e);});
const vnodes=DATA.nodes.map(n=>({
  id:n.id,
  label:n.label+(n.attributes.length?"\n—\n"+n.attributes.join("\n"):""),
  shape:"box", borderWidth:2,
  color:{background:"#e3f2fd", border:"#1565c0"},
  font:{size:13, align:"left"}}));
const vedges=DATA.edges.map(e=>({
  from:e.from, to:e.to,
  label:e.label+(e.cardinality?"  ["+e.cardinality+"]":""),
  arrows:"to", font:{size:11, color:"#64748b", strokeWidth:4},
  color:{color:"#94a3b8"}, smooth:{type:"continuous"}}));
WF.draw(vnodes, vedges);
function showAll(){
  WF.panelHead("All entities ("+DATA.nodes.length+")");
  WF.panel("<table>"+DATA.nodes.map(n=>
    `<tr class="row" onclick="showOne('${n.id}')"><td class="e">${n.label}</td>`+
    `<td>${n.note||"<span class=empty>—</span>"}`+
    `${n.attributes.length?`<div class="rq">${n.attributes.join("<br>")}</div>`:""}</td></tr>`).join("")+"</table>");
}
function showOne(id){const n=N[id]; if(!n) return;
  WF.panelHead("Entity");
  const rel=(arr,out)=>(arr||[]).map(e=>{const o=out?e.to:e.from;
    const card=e.cardinality?` [${e.cardinality}]`:"";
    return `<div class="rel">${out?"":"← "}<b>${N[o]?N[o].label:o}</b> <span class="lbl">${e.label}${card}</span></div>`;
  }).join("")||"<div class=empty>none</div>";
  WF.panel(`<div class="nm">${n.label}</div>`+
    `<div class="desc">${n.note||"<span class=empty>no note</span>"}</div>`+
    (n.attributes.length?`<div class="grp">Attributes</div><div class="rq">${n.attributes.join("<br>")}</div>`:"")+
    `<div class="grp">Relations out</div>${rel(OUT[id],true)}`+
    `<div class="grp">Relations in</div>${rel(INC[id],false)}`);
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
    comps, ents = d.get("components"), d.get("entities")
    if comps is not None and ents is not None:
        fail("give 'components' (design mode) OR 'entities' (entity mode), not both")
    picked = ents if comps is None else comps
    if not isinstance(picked, list) or not picked:
        fail("design needs a non-empty 'components' or 'entities' list")
    return d


def warn(msg):
    sys.stderr.write(f"render_design: warning: {msg}\n")


def _default_config():
    """The host's .wf/config.yaml, via the CLI's resolver (worktree-safe). A standalone
    copy with no CLI beside it simply has no default — every input is then explicit."""
    sys.path.insert(0, os.path.join(_HERE, "..", "cli"))
    try:
        import common  # noqa: PLC0415
        return common.default_config()
    except Exception:
        return ""


def config_defaults(config_path):
    """(model, subsystems, [test roots]) from .wf/config.yaml, or empties if there is none.

    discover writes the model and subsystem map to config-declared locations and the test
    roots are config-declared too, so none of these need an agent to type them. Resolution
    goes through the CLI's shared config reader — the one config reader wf2 has.
    """
    if not config_path or not os.path.exists(config_path):
        return None, None, []
    sys.path.insert(0, os.path.join(_HERE, "..", "cli"))
    try:
        import common  # noqa: PLC0415 — optional: only a wf-installed repo has it
    except Exception as e:  # a standalone copy, or PyYAML absent
        warn(f"cannot read {config_path} ({e}); rendering without derived context")
        return None, None, []
    paths = (common.config_doc(config_path).get("paths") or {})
    root = common.project_root(config_path)
    def under(rel):
        return str(root / rel) if rel else None
    roots = paths.get("tests") or []
    if isinstance(roots, str):
        roots = [roots]
    return (under(paths.get("discover_model")),
            under(paths.get("discover_subsystems")),
            [under(r) for r in roots])


def load_json_file(path, what, required=True):
    """Read a JSON input. A path the caller named explicitly must exist — honour the
    intent or fail. A path that merely came from config may legitimately not exist yet
    (discover has not run), so warn and degrade rather than blocking the render.
    """
    if not os.path.exists(path):
        if required:
            fail(f"{what} not found: {path}")
        warn(f"{what} not found: {path} — rendering without it")
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        fail(f"cannot read {what} ({path}): {e}")


def model_index(model_path, required=True):
    """{component key -> {path, lang, loc, synopsis, uid}} from discover's model.json.

    Keyed by BOTH the node's uid ("go:internal/auth") and its bare id ("internal/auth"),
    because the agent authors the bare id while discover's descriptions are uid-keyed.
    """
    if not model_path:
        return {}
    doc = load_json_file(model_path, "the discover model", required)
    if doc is None:
        return {}
    nodes = doc.get("nodes") or {}
    nodes = list(nodes.values()) if isinstance(nodes, dict) else nodes
    idx = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        entry = {"uid": n.get("uid", ""), "path": n.get("path", ""),
                 "lang": n.get("lang", ""), "loc": n.get("loc", ""),
                 "synopsis": n.get("synopsis", "")}
        for key in (n.get("uid"), n.get("id")):
            if key:
                idx.setdefault(key, entry)
    return idx


def description_index(desc_path, required=True):
    """{uid -> prose description} from discover's subsystems.json."""
    if not desc_path:
        return {}
    doc = load_json_file(desc_path, "the discover subsystem map", required)
    if doc is None:
        return {}
    cd = doc.get("component_descriptions") or {}
    return cd if isinstance(cd, dict) else {}


def harvest_tags(tests_roots, test_globs, required=True):
    """[{id, statement, file}] for every proving tag under the given roots.

    harvest() reports each file relative to its own root, so re-join the root to get a
    repo path that can be matched against a component's path.
    """
    if not tests_roots:
        warn("no test roots (pass --tests, or set paths.tests in .wf/config.yaml) — "
             "the system tests already proven are NOT shown")
        return {}
    records = {}
    for root in tests_roots:
        if not os.path.isdir(root):
            if required:
                fail(f"--tests {root} is not a directory")
            warn(f"test root not found: {root} — skipping it")
            continue
        for rid, entry in harvest([root], test_globs).items():
            rec = records.setdefault(rid, {"id": rid, "statement": "", "files": []})
            for text in entry["statements"]:
                if text and not rec["statement"]:
                    rec["statement"] = text
            for rel in entry["files"]:
                rec["files"].append(os.path.normpath(os.path.join(root, rel)))
    for rec in records.values():
        rec["files"] = sorted(set(rec["files"]))
    return records


def build_data(design, model=None, descriptions=None, tags=None, tags_scanned=True):
    comps = design["components"]
    deps = design.get("dependencies", []) or []
    allocation = design.get("allocation", []) or []
    model = model or {}
    descriptions = descriptions or {}
    tags = tags or {}

    reqs = {}
    for a in allocation:
        if not a.get("requirement"):
            continue
        reqs.setdefault(a.get("component"), []).append(
            {"id": a["requirement"], "statement": a.get("statement", "")})

    nodes = []
    for c in comps:
        cid = c.get("id") or c.get("label")
        m = model.get(cid, {})
        # What the component IS, derived — discover's prose, else its mechanical synopsis.
        # The authored note rides alongside as what THIS CHANGE does to it; the two are
        # different facts, so neither overwrites the other.
        desc = descriptions.get(m.get("uid") or cid, "") or m.get("synopsis", "")
        nodes.append({
            "id": cid,
            "label": c.get("label", cid),
            "state": c.get("state", "existing"),
            "desc": desc,
            "note": c.get("note", ""),
            "path": m.get("path", ""),
            "lang": m.get("lang", ""),
            "loc": m.get("loc", ""),
            "reqs": reqs.get(cid, []),
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

    sys_new = []
    for t in design.get("system_tests", []) or []:
        if not t.get("id"):
            fail("every system test case needs an 'id'")
        sys_new.append({
            "id": t["id"], "title": t.get("title", ""), "covers": t.get("covers", ""),
            "given": t.get("given", ""), "when": t.get("when", ""), "then": t.get("then", ""),
            "components": [c for c in (t.get("components") or []) if c],
        })
    sys_shipped = [tags[k] for k in sorted(tags) if k.startswith("SYS-TC-")
                   and k not in {t["id"] for t in sys_new}]

    decisions = []
    for d in design.get("decisions", []) or []:
        if not d.get("title"):
            fail("every decision needs a 'title'")
        decisions.append({
            "id": d.get("id", ""), "title": d["title"], "question": d.get("question", ""),
            "options": [{"label": o.get("label", ""), "pros": o.get("pros", ""),
                         "cons": o.get("cons", "")} for o in (d.get("options") or [])],
            "recommended": d.get("recommended", ""), "status": d.get("status", "open"),
            "components": [c for c in (d.get("components") or []) if c],
        })

    return {
        "nodes": nodes, "edges": edges,
        "sys_new": sys_new, "sys_shipped": sys_shipped,
        "decisions": decisions,
        # False = the tags were never harvested, so "already proven" is UNKNOWN here.
        # Rendering it as an empty list would assert the system promises nothing.
        "tags_scanned": tags_scanned,
        "node_palette": {k: list(v) for k, v in NODE_STYLE.items()},
        "edge_palette": {k: list(v) for k, v in EDGE_STYLE.items()},
    }


def build_er_data(design):
    nodes = []
    for e in design["entities"]:
        eid = e.get("id") or e.get("label")
        if not eid:
            fail("every entity needs an 'id' or 'label'")
        nodes.append({
            "id": eid,
            "label": e.get("label", eid),
            "attributes": [a for a in (e.get("attributes") or []) if a],
            "note": e.get("note", ""),
        })

    edges = []
    for r in design.get("relations", []) or []:
        if not r.get("from") or not r.get("to"):
            fail("every relation needs 'from' and 'to'")
        if not r.get("label"):
            fail("every relation needs a 'label' — an unlabelled ER edge says nothing")
        edges.append({
            "from": r["from"], "to": r["to"],
            "label": r["label"],
            "cardinality": r.get("cardinality", ""),
        })

    return {"nodes": nodes, "edges": edges}


def main():
    ap = argparse.ArgumentParser(description="Render an SA design graph to self-contained HTML.")
    ap.add_argument("--out", required=True, help="output HTML path")
    ap.add_argument("--config", default=_default_config(),
                    help="path to .wf/config.yaml — supplies --model/--descriptions/--tests "
                         "when they are not given (default: host-anchored)")
    ap.add_argument("--model", help="discover model.json — resolves each component's real "
                                    "path, language and synopsis (default: paths.discover_model)")
    ap.add_argument("--descriptions", help="discover subsystems.json — each component's "
                                           "prose description (default: paths.discover_subsystems)")
    ap.add_argument("--tests", action="append", metavar="PATH",
                    help="test-tree root to harvest shipped [SYS-TC:] tags from "
                         "(repeatable; default: every root in paths.tests)")
    ap.add_argument("--test-glob", action="append", dest="test_glob", metavar="GLOB",
                    help="extra test-file name glob, added to the defaults (repeatable)")
    args = ap.parse_args()

    design = load_design()
    if "entities" in design:
        data, summary, bar, body_js, kind = (
            build_er_data(design), ER_SUMMARY, ER_BAR, ER_BODY_JS, "entities")
    else:
        # These three are mechanical: discover writes the model and subsystem map to
        # config-declared paths, and the test roots are config-declared. Fall back to
        # config so a caller never has to restate them; an explicit flag still wins, and
        # only an explicit flag is treated as intent worth failing over.
        cfg_model, cfg_desc, cfg_tests = config_defaults(args.config)
        globs = DEFAULT_TEST_GLOBS + tuple(args.test_glob or ())
        data, summary, bar, body_js, kind = (
            build_data(design,
                       model=model_index(args.model or cfg_model, required=bool(args.model)),
                       descriptions=description_index(args.descriptions or cfg_desc,
                                                      required=bool(args.descriptions)),
                       tags=harvest_tags(args.tests or cfg_tests, globs,
                                         required=bool(args.tests)),
                       tags_scanned=bool(args.tests or cfg_tests)),
            SUMMARY, BAR, BODY_JS, "components")
    page = chassis.render_page(
        title=design.get("title", "design"),
        summary=summary,
        bar_html=bar,
        panel_html="",
        body_js=body_js,
        data=data,
        extra_css=EXTRA_CSS,
    )

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print(f"render_design: wrote {args.out} "
          f"({len(data['nodes'])} {kind}, {len(data['edges'])} edges)")


if __name__ == "__main__":
    main()
