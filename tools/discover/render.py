#!/usr/bin/env python3
"""render.py — interactive two-level read-view for human planning.

Reads the spine's merged model.json + the scout's reconciled subsystem map and emits
ONE self-contained interactive HTML page:

  Level 1 (default): a graph of SUBSYSTEMS and how they connect. Nothing else.
  Level 2 (click a subsystem): that subsystem's COMPONENT view — internal component
           interactions PLUS cross-subsystem interactions (the external component +
           which subsystem it lives in). Click an external node to jump there.

Every subsystem and every component carries a 1-2 sentence description (doc-derived
where the structure layer had one, scout-derived otherwise). Nothing is durable:
regenerated from code + the (cache-owned) subsystem map on demand.

Usage:  render.py --model model.json --subsystems subsystems.json --out view.html [--title T]
"""
import argparse, json, html, os
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--subsystems", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="System read-view")
    a = ap.parse_args()

    model = json.load(open(a.model))
    nodes = model["nodes"]
    sub = json.load(open(a.subsystems))
    subsystems = sub["subsystems"]
    desc = sub.get("component_descriptions", {})

    sub_of = {}
    for i, s in enumerate(subsystems):
        for u in s["members"]:
            sub_of[u] = i

    # description per component: scout desc -> doc synopsis -> honest blank
    def cdesc(u):
        d = (desc.get(u) or "").strip()
        if d:
            return d
        return (nodes[u].get("synopsis") or "").strip()

    cnodes = {}
    for u, n in nodes.items():
        # provenance: doc-grounded if the structure layer had a synopsis, else inferred
        prov = "doc" if (n.get("synopsis") or "").strip() else "scout"
        cnodes[u] = {"name": n["name"], "path": n["path"], "loc": n["loc"], "lang": n["lang"],
                     "has_doc": n["has_doc"], "has_tests": n["has_tests"], "prov": prov,
                     "deps": [d for d in n["deps"] if d in nodes],
                     "sub": sub_of.get(u, -1), "desc": cdesc(u)}

    inter = defaultdict(int)
    for u, n in cnodes.items():
        si = n["sub"]
        for d in n["deps"]:
            sj = cnodes[d]["sub"]
            if si >= 0 and sj >= 0 and si != sj:
                inter[(si, sj)] += 1

    disagreements = sub.get("disagreements", [])
    # SOFT edges: couplings the scout surfaced that static imports can't show (e.g. a
    # cross-language API contract via git co-change). Connect the subsystems a single
    # disagreement spans, so the system graph isn't two disconnected language islands.
    soft = set()
    for d in disagreements:
        spans = sorted({sub_of[u] for u in d.get("components", []) if u in sub_of})
        for i in range(len(spans)):
            for j in range(i + 1, len(spans)):
                soft.add((spans[i], spans[j]))

    subs_out = []
    for i, s in enumerate(subsystems):
        mem = [u for u in s["members"] if u in cnodes]
        subs_out.append({"id": i, "name": s["name"], "desc": s.get("summary", ""),
                         "basis": s.get("basis", ""),
                         "loc": sum(cnodes[u]["loc"] for u in mem),
                         "langs": sorted({cnodes[u]["lang"] for u in mem}),
                         "members": mem})

    data = {"title": a.title, "system_summary": sub.get("system_summary", ""),
            "subsystems": subs_out, "nodes": cnodes,
            "inter": [[i, j, w] for (i, j), w in inter.items()],
            "soft_edges": [[i, j] for (i, j) in sorted(soft)],
            "disagreements": disagreements}

    out = TEMPLATE.replace("__TITLE__", html.escape(a.title)) \
                  .replace("__DATA__", json.dumps(data))

    # Inject the graph library LAST (so its bytes aren't touched by the substitutions
    # above). Vendored = the page is 100% offline/self-contained; CDN = needs internet.
    vis = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "vis-network.min.js")
    if os.path.exists(vis):
        lib = "<script>\n" + open(vis).read() + "\n</script>"
        offline = True
    else:
        lib = '<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>'
        offline = False
    out = out.replace("__VISLIB__", lib)

    open(a.out, "w").write(out)
    print(f"wrote {a.out}  ({len(cnodes)} components, {len(subs_out)} subsystems, "
          f"{'offline/self-contained' if offline else 'CDN — needs internet'})")


TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__</title>
__VISLIB__
<style>
  :root{--go:#3b82f6;--ts:#f59e0b;--ghost:#cbd5e1;--bg:#f1f5f9;--card:#fff;--line:#e2e8f0;--ink:#0f172a;--mut:#64748b}
  *{box-sizing:border-box} html,body{margin:0;height:100%;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:var(--ink)}
  body{display:flex;flex-direction:column;height:100vh;background:var(--bg)}
  header{padding:14px 22px;background:var(--card);border-bottom:1px solid var(--line);flex:none}
  h1{margin:0;font-size:17px} .sum{color:var(--mut);font-size:13px;max-width:120ch;margin-top:4px}
  .bar{display:flex;align-items:center;gap:10px;padding:8px 22px;background:var(--card);border-bottom:1px solid var(--line);flex:none;font-size:13px}
  .crumb{font-weight:600} .crumb .sep{color:var(--mut);font-weight:400;margin-right:4px}
  button{font:13px inherit;padding:4px 12px;border:1px solid var(--line);background:#fff;border-radius:6px;cursor:pointer}
  button:hover{border-color:var(--go);background:#eff6ff} button:disabled{opacity:.4;cursor:default}
  .legend{margin-left:auto;color:var(--mut);font-size:12px;display:flex;gap:14px;align-items:center}
  .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;vertical-align:middle}
  .wrap{flex:1;display:flex;min-height:0}
  #graph{flex:1;min-width:0;background:var(--bg)}
  aside{width:370px;flex:none;border-left:1px solid var(--line);background:var(--card);overflow:auto;padding:18px}
  aside h2{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 8px}
  .ph{color:var(--mut);font-size:13px}
  .nm{font:15px ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:600;word-break:break-all}
  .pth{font:11px ui-monospace,monospace;color:var(--mut);word-break:break-all;margin-bottom:8px}
  .meta{font-size:12px;color:var(--mut);margin-bottom:10px}
  .desc{font-size:13px;margin:8px 0 14px;line-height:1.55}
  .grp{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:14px 0 6px}
  .row{font:12px ui-monospace,monospace;padding:3px 0;cursor:pointer;border-bottom:1px solid #f1f5f9}
  .row:hover{color:var(--go)} .row .st{font-family:inherit;color:var(--mut);font-size:11px;float:right}
  .empty{color:#cbd5e1;font-style:italic;font-size:12px}
  .basis{font-size:11px;color:#94a3b8;border-top:1px dashed var(--line);margin-top:12px;padding-top:8px}
  .dis{background:#fffbeb;border:1px solid #fde68a;border-radius:7px;padding:8px 10px;margin-top:10px;font-size:12px;color:#78350f}
  .hint{font-size:11px;color:var(--mut);margin-top:6px}
</style></head><body>
<header><h1 id="title"></h1><div class="sum" id="summary"></div></header>
<div class="bar">
  <button id="back" onclick="goSystem()" disabled>← System</button>
  <span class="crumb" id="crumb"></span>
  <span class="legend">
    <span><span class="dot" style="background:var(--go)"></span>Go</span>
    <span><span class="dot" style="background:var(--ts)"></span>TS</span>
    <span><span class="dot" style="background:var(--ghost)"></span>other subsystem</span>
    <span id="leg-edges"></span>
  </span>
</div>
<div class="wrap">
  <div id="graph"></div>
  <aside><h2 id="panel-h">Details</h2><div id="panel"></div></aside>
</div>
<script>
const D = __DATA__;
const N = D.nodes, S = D.subsystems;
const LANGCOL = {go:"#3b82f6", ts:"#f59e0b"};
const langCol = l => LANGCOL[l] || "#8b5cf6";
document.getElementById("title").textContent = D.title;
document.getElementById("summary").textContent = D.system_summary;

const RDEP = {}; for(const u in N){ for(const d of N[u].deps){ (RDEP[d]=RDEP[d]||[]).push(u); } }
const subName = i => i>=0 ? S[i].name : "—";

let network=null, level="system", curSub=-1;
const container=document.getElementById("graph");
const baseOpts={
  interaction:{hover:true, tooltipDelay:120},
  physics:{stabilization:true, barnesHut:{springLength:150, avoidOverlap:0.5, gravitationalConstant:-6000}},
  nodes:{shape:"dot", font:{size:13, multi:false, face:"-apple-system,Segoe UI,Roboto"}, borderWidth:1},
  edges:{color:{color:"#cbd5e1", highlight:"#3b82f6"}, smooth:{type:"continuous"}, width:1}
};
const sizeFor = loc => 12 + Math.min(40, Math.sqrt(loc)/3);

function draw(nodes, edges){
  const data={nodes:new vis.DataSet(nodes), edges:new vis.DataSet(edges)};
  if(!network){
    network=new vis.Network(container, data, baseOpts);
    network.on("click", p=>{ if(p.nodes.length) onClick(p.nodes[0]); });
  } else { network.setData(data); }
}

function goSystem(){
  level="system"; curSub=-1;
  document.getElementById("back").disabled=true;
  document.getElementById("crumb").innerHTML="<b>System</b> — "+S.length+" subsystems";
  document.getElementById("leg-edges").textContent="— solid = imports · dashed purple = co-change coupling (no import)";
  const nodes=S.map(s=>({
    id:"s"+s.id, label:s.name+"\n("+s.members.length+")", value:sizeFor(s.loc),
    color: s.langs.length>1?"#8b5cf6":langCol(s.langs[0]), title:s.name
  }));
  // ALL static edges (width by weight — weight-1 links are real, don't hide them)
  const edges=D.inter.map(e=>({from:"s"+e[0], to:"s"+e[1], arrows:"to",
    width:1+Math.min(6,Math.log2(e[2]+1)), title:e[2]+" reference"+(e[2]>1?"s":"")}));
  // soft coupling edges (co-change / contract) the import graph can't show
  for(const [i,j] of D.soft_edges){
    edges.push({from:"s"+i, to:"s"+j, dashes:true, color:{color:"#a78bfa"}, width:2,
      title:"co-change coupling (no static import)"});
  }
  draw(nodes, edges);
  showSystem();
}

function showSystem(){
  document.getElementById("panel-h").textContent="System";
  const dis=(D.disagreements||[]).map(d=>
    `<div class="dis">${d.finding}<div style="margin-top:5px">${(d.components||[]).map(u=>
      N[u]?`<span class="row" style="display:inline;border:0;padding:0 6px 0 0" onclick="jump('${u}')">${N[u].name}</span>`:"").join("")}</div></div>`).join("");
  document.getElementById("panel").innerHTML=`
    <div class="desc">${D.system_summary}</div>
    <div class="hint">Click any subsystem node to enter its component view.</div>
    <div class="grp">Cross-signal couplings (${(D.disagreements||[]).length})</div>
    <div class="hint" style="margin:0 0 6px">where folder / import / git-history clusterings disagree — often the highest-blast-radius edits</div>
    ${dis||"<div class='empty'>none</div>"}`;
}

function jump(u){ const n=N[u]; if(!n) return; if(n.sub>=0) enterSub(n.sub); showComp(u); }

function enterSub(i){
  level="component"; curSub=i; const s=S[i];
  document.getElementById("back").disabled=false;
  document.getElementById("crumb").innerHTML="<span class='sep'>System ▸</span><b>"+s.name+"</b> — "+s.members.length+" components";
  document.getElementById("leg-edges").textContent="— solid = internal · dashed grey = calls out · dashed red = called from outside";
  const mem=new Set(s.members);
  const nodes=[], edges=[], ext=new Set();
  for(const u of s.members){
    const n=N[u];
    nodes.push({id:u, label:n.name, value:sizeFor(n.loc), color:langCol(n.lang), title:n.path});
  }
  for(const u of s.members){
    for(const d of N[u].deps){
      if(mem.has(d)){ edges.push({from:u,to:d,arrows:"to"}); }
      else { ext.add(d); edges.push({from:u,to:d,arrows:"to",dashes:true,color:{color:"#cbd5e1"}}); }
    }
    for(const r of (RDEP[u]||[])){
      if(!mem.has(r)){ ext.add(r); edges.push({from:r,to:u,arrows:"to",dashes:true,color:{color:"#fca5a5"}}); }
    }
  }
  for(const e of ext){
    const n=N[e];
    nodes.push({id:e, label:n.name+"\n["+subName(n.sub)+"]", color:"#e2e8f0",
      font:{color:"#475569"}, value:10, title:n.path+" — "+subName(n.sub)+" (click to open)"});
  }
  draw(nodes, edges);
  showSub(i, false);
}

function onClick(id){
  if(/^s\d+$/.test(id)){ const i=+id.slice(1); if(level==="system") enterSub(i); else showSub(i,false); return; }
  const n=N[id]; if(!n) return;
  if(n.sub!==curSub && n.sub>=0){ enterSub(n.sub); }   // external node -> jump to its subsystem
  showComp(id);
}

function showSub(i, atSystem){
  if(i<0) return;
  const s=S[i];
  document.getElementById("panel-h").textContent="Subsystem";
  document.getElementById("panel").innerHTML=`
    <div class="nm">${s.name}</div>
    <div class="meta">${s.members.length} components · ${s.loc.toLocaleString()} LOC · ${s.langs.join("/")}</div>
    <div class="desc">${s.desc||"<span class='empty'>no description</span>"}</div>
    ${atSystem?`<div class="hint">Click any subsystem node to enter its component view.</div>`:""}
    <div class="grp">Components (by size)</div>
    ${s.members.slice().sort((a,b)=>N[b].loc-N[a].loc).map(u=>
      `<div class="row" onclick="showComp('${u}')">${N[u].name}<span class="st">${N[u].loc} LOC</span></div>`).join("")}
    <div class="basis"><b>why grouped:</b> ${s.basis||"—"}</div>`;
}

function showComp(u){
  const n=N[u];
  document.getElementById("panel-h").textContent="Component";
  const link = v => `<div class="row" onclick="showComp('${v}')">${N[v].name}<span class="st">${subName(N[v].sub)}</span></div>`;
  const outs=n.deps.slice().sort(), ins=(RDEP[u]||[]).slice().sort();
  document.getElementById("panel").innerHTML=`
    <div class="nm">${n.name}</div>
    <div class="pth">${n.path} · ${n.lang} · ${n.loc} LOC</div>
    <div class="meta">${n.has_doc?"📄 documented":"○ no doc"} · ${n.has_tests?"✓ tested":"△ no tests"} · ${subName(n.sub)}</div>
    <div class="desc">${n.desc||"<span class='empty'>no description — structure layer is silent here</span>"}
      <div class="hint" style="margin-top:4px">${n.prov==="doc"?"📄 doc-grounded":"✨ scout-inferred from signatures"}</div></div>
    <div class="grp">Depends on (${outs.length})</div>${outs.length?outs.map(link).join(""):"<div class='empty'>none</div>"}
    <div class="grp">Depended on by (${ins.length})</div>${ins.length?ins.map(link).join(""):"<div class='empty'>none</div>"}`;
}

goSystem();
</script></body></html>
"""

if __name__ == "__main__":
    main()
