#!/usr/bin/env python3
"""chassis.py — the shared rendering shell for wf's interactive graph views.

Both the discover read-view (render.py) and the SA design-view (render_design.py)
draw a node/edge graph beside a details side panel, offline and self-contained.
Everything common to that lives here exactly once: the page skeleton, the design
system CSS, the two-pane layout (graph left, panel right), the vendored graph lib
inlined for offline use, and the base vis-network options (the spacing physics).

A caller supplies only what differs — its title, the bar content, the initial panel
HTML, any view-specific CSS, and a body-JS string that builds its own nodes/edges and
wires clicks to the panel. The JS preamble this module injects exposes:

  WF.draw(nodes, edges)   create/replace the vis.Network in #graph (base options applied)
  WF.onNode(fn)           register a click handler called with the clicked node id
  WF.panel(html)          set the side-panel body
  WF.panelHead(text)      set the side-panel heading
  WF.baseOptions          the shared vis options (spacing) — spread to tweak per call

Usage (from a renderer):
  import chassis
  html = chassis.render_page(title=..., summary=..., bar_html=..., panel_html=...,
                             extra_css=..., body_js=..., data=<json-serialisable>)
"""
import html as _html
import json
import os

# The vendored lib lives beside this module so it is vendored once, not per renderer.
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "vendor", "vis-network.min.js")


def vis_lib_block():
    """Inline the vendored vis-network for a fully offline page; CDN fallback."""
    if os.path.exists(_VENDOR):
        with open(_VENDOR, encoding="utf-8") as fh:
            return "<!-- vis-network inlined (offline) -->\n<script>\n" + fh.read() + "\n</script>"
    return ('<script src="https://unpkg.com/vis-network@9.1.9/'
            'standalone/umd/vis-network.min.js"></script>')


# Design system + two-pane layout shared by every wf graph view.
_BASE_CSS = """
 :root{--bg:#f1f5f9;--card:#fff;--line:#e2e8f0;--ink:#0f172a;--mut:#64748b;--go:#3b82f6}
 *{box-sizing:border-box}
 html,body{margin:0;height:100%;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:var(--ink)}
 body{display:flex;flex-direction:column;height:100vh;background:var(--bg)}
 header{padding:14px 22px;background:var(--card);border-bottom:1px solid var(--line);flex:none}
 h1{margin:0;font-size:17px} .sum{color:var(--mut);font-size:13px;margin-top:4px;max-width:130ch}
 .bar{display:flex;align-items:center;gap:14px;padding:8px 22px;background:var(--card);border-bottom:1px solid var(--line);flex:none;font-size:12px;color:var(--mut)}
 .bar .legend{margin-left:auto;display:flex;gap:14px;align-items:center}
 button{font:12px inherit;padding:4px 12px;border:1px solid var(--line);background:#fff;border-radius:6px;cursor:pointer}
 button:hover{border-color:var(--go);background:#eff6ff} button:disabled{opacity:.4;cursor:default}
 .wrap{flex:1;display:flex;min-height:0}
 #graph{flex:1;min-width:0;background:var(--bg)}
 aside{width:400px;flex:none;border-left:1px solid var(--line);background:var(--card);overflow:auto;padding:18px}
 aside h2{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 10px}
 .nm{font:15px ui-monospace,Menlo,monospace;font-weight:600;word-break:break-all}
 .desc{font-size:13px;margin:8px 0 14px;line-height:1.5}
 .grp{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:14px 0 6px}
 .row{cursor:pointer;border-bottom:1px solid #f1f5f9}
 .row:hover{color:var(--go)}
 .chip{display:inline-block;padding:1px 8px;border-radius:4px;border:2px solid;font-size:12px}
 .empty{color:#cbd5e1;font-style:italic}
"""

# JS preamble: the WF helper the body-JS builds on. Owns the network + base options.
_PREAMBLE = """
const WF = (function(){
  const baseOptions = {
    interaction:{hover:true, tooltipDelay:120},
    physics:{stabilization:true, barnesHut:{springLength:150, avoidOverlap:0.5, gravitationalConstant:-6000}},
    nodes:{shape:"dot", font:{size:13, multi:false, face:"-apple-system,Segoe UI,Roboto"}, borderWidth:1},
    edges:{color:{color:"#cbd5e1", highlight:"#3b82f6"}, smooth:{type:"continuous"}, width:1}
  };
  const el = document.getElementById("graph");
  let net=null, nodeHandler=null;
  function draw(nodes, edges){
    const data={nodes:new vis.DataSet(nodes), edges:new vis.DataSet(edges)};
    if(!net){ net=new vis.Network(el, data, baseOptions);
      net.on("click", p=>{ if(nodeHandler && p.nodes.length) nodeHandler(p.nodes[0]); }); }
    else net.setData(data);
  }
  return {
    baseOptions, draw,
    onNode:fn=>{nodeHandler=fn;},
    panel:h=>{document.getElementById("panel").innerHTML=h;},
    panelHead:t=>{document.getElementById("panel-h").textContent=t;}
  };
})();
"""

_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>__TITLE__</title>
__VISLIB__
<style>__CSS__</style></head><body>
<header><h1>__TITLE__</h1><div class="sum">__SUMMARY__</div></header>
<div class="bar">__BAR__</div>
<div class="wrap"><div id="graph"></div>
 <aside><h2 id="panel-h">Details</h2><div id="panel">__PANEL__</div></aside></div>
<script>
const DATA = __DATA__;
__PREAMBLE__
__BODY__
</script></body></html>"""


def render_page(*, title, summary, bar_html, panel_html, body_js,
                data=None, extra_css=""):
    """Assemble one self-contained offline page from the caller's parts.

    title/summary    header text (escaped)
    bar_html         the controls/legend bar (caller-built HTML)
    panel_html       initial side-panel body HTML
    body_js          the caller's JS — builds nodes/edges, calls WF.draw/onNode/panel
    data             JSON-serialisable payload exposed to body_js as the global DATA
    extra_css        view-specific CSS appended after the base design system
    """
    page = (_TEMPLATE
            .replace("__TITLE__", _html.escape(title))
            .replace("__SUMMARY__", summary)
            .replace("__CSS__", _BASE_CSS + extra_css)
            .replace("__BAR__", bar_html)
            .replace("__PANEL__", panel_html)
            .replace("__DATA__", json.dumps(data if data is not None else {}))
            .replace("__PREAMBLE__", _PREAMBLE)
            .replace("__BODY__", body_js))
    # Inject the lib LAST so the substitutions above never run over its bytes.
    return page.replace("__VISLIB__", vis_lib_block())
