#!/usr/bin/env python3
"""spine.py — the language-agnostic merge layer of the read-view (ported from Go).

wf's own machinery, so it is Python: every wf user runs the spine regardless of the
repo's language, so it must not drag in a Go toolchain. Its ONLY input is the common
IR JSON (read-view-extractor-guide §4) emitted by the per-language extractors (which
DO live in their own language — a Go dev has Go, a TS dev has Node). Nothing here is
durable: the model is derived from code on demand and thrown away.

  merge  : union N IR JSON files into one resolved model.json (the canonical merge)
  system : render a merged model (or IRs) as the compressed text system map

Usage:
  spine.py merge  A.ir.json B.ir.json -o model.json [--title T]
  spine.py system model.json            # or: spine.py system A.ir.json B.ir.json
"""
import argparse, json, sys
from collections import defaultdict


def last_seg(p):
    return p.rsplit("/", 1)[-1]


def merge(irs):
    """Union the components of every IR into one model. Each node is tagged with its
    source language; ids are namespaced into a UID (lang:id). Intra-language
    depends_on edges resolve to the target's UID (cross-language edges do not exist in
    code — backends and frontends talk over HTTP, not imports)."""
    nodes = {}                       # uid -> node
    order = []                       # uids in stable order
    by_id = defaultdict(dict)        # lang -> {id : uid}        (authoritative)
    by_name = defaultdict(dict)      # lang -> {name|lastseg : uid}  (fallback only)
    langs = []

    for ir in irs:
        lang = ir.get("language") or "unknown"
        if lang not in langs:
            langs.append(lang)
        for c in ir.get("components", []):
            uid = f"{lang}:{c['id']}"
            n = {
                "uid": uid, "id": c["id"], "name": c.get("name") or last_seg(c["id"]),
                "path": c.get("path") or c["id"], "loc": c.get("loc", 0),
                "kind": c.get("kind", ""), "lang": lang, "module": ir.get("module", ""),
                "synopsis": c.get("synopsis", ""),
                "has_doc": c.get("has_doc", False), "has_tests": c.get("has_tests", False),
                "types": c.get("types", []) or [], "functions": c.get("functions", []) or [],
                "_raw_deps": c.get("depends_on", []) or [],
            }
            nodes[uid] = n
            order.append(uid)
            by_id[lang][c["id"]] = uid                       # exact id wins
            by_name[lang].setdefault(n["name"], uid)         # name is fallback
            by_name[lang].setdefault(last_seg(c["id"]), uid)

    # resolve depends_on to UIDs (intra-language only); exact id beats name match.
    for uid in order:
        n = nodes[uid]
        deps, seen = [], set()
        bi, bn = by_id[n["lang"]], by_name[n["lang"]]
        for d in n.pop("_raw_deps"):
            t = bi.get(d) or bn.get(d) or bi.get(last_seg(d)) or bn.get(last_seg(d))
            if t and t != uid and t not in seen:
                deps.append(t); seen.add(t)
        n["deps"] = sorted(deps)

    return {"languages": sorted(langs), "nodes": nodes,
            "order": sorted(order, key=lambda u: (nodes[u]["lang"], nodes[u]["id"]))}


def load_irs(paths):
    irs = []
    for p in paths:
        try:
            ir = json.load(open(p))
            if ir.get("components"):
                irs.append(ir)
            else:
                print(f"warning: {p} has no components, skipping", file=sys.stderr)
        except Exception as e:
            print(f"warning: skipping {p}: {e}", file=sys.stderr)
    return irs


def is_model(path):
    try:
        d = json.load(open(path))
        return isinstance(d, dict) and "nodes" in d and "languages" in d
    except Exception:
        return False


def render_system(model, title):
    nodes, order = model["nodes"], model["order"]
    total_loc = sum(n["loc"] for n in nodes.values())
    out = [f"SYSTEM READ-VIEW · {title}",
           "merged from %d language IR(s) — derived from code, disposable, regenerate on demand"
           % len(model["languages"]),
           f"{len(nodes)} components · {total_loc} LOC total", ""]
    for lang in model["languages"]:
        ln = [nodes[u] for u in order if nodes[u]["lang"] == lang]
        lloc = sum(n["loc"] for n in ln)
        out.append(f"── {lang.upper()} ──  {len(ln)} components · {lloc} LOC")
        for n in sorted(ln, key=lambda n: -n["loc"]):
            syn = n["synopsis"] or "(no synopsis)"
            out.append(f"  {n['id']:<34} {n['loc']:>6}  {syn}")
            if n["deps"]:
                deps = ", ".join(sorted(last_seg(nodes[d]['id']) for d in n["deps"]))
                out.append(f"  {'':<34} {'':>6}  → {deps}")
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge"); m.add_argument("irs", nargs="+"); m.add_argument("-o", required=True)
    m.add_argument("--title", default="")
    s = sub.add_parser("system"); s.add_argument("inputs", nargs="+"); s.add_argument("--title", default="")
    a = ap.parse_args()

    if a.cmd == "merge":
        irs = load_irs(a.irs)
        if not irs:
            print("error: no readable IR files", file=sys.stderr); sys.exit(1)
        model = merge(irs)
        model["title"] = a.title or " + ".join(f"{ir.get('module','?')} ({ir.get('language','?')})" for ir in irs)
        json.dump(model, open(a.o, "w"), indent=2)
        print(f"merged {len(irs)} IR(s) → {len(model['nodes'])} components → {a.o}", file=sys.stderr)

    elif a.cmd == "system":
        if len(a.inputs) == 1 and is_model(a.inputs[0]):
            model = json.load(open(a.inputs[0]))
        else:
            model = merge(load_irs(a.inputs))
            model["title"] = a.title
        print(render_system(model, a.title or model.get("title", "system")))


if __name__ == "__main__":
    main()
