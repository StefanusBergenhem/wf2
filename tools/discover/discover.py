#!/usr/bin/env python3
"""discover.py — the MECHANICAL spine of discovery (Python; wf's own machinery).

Runs the deterministic, LLM-free half of discovery and leaves the result in a
transient output dir:

  per-language extractor (in its own language)  →  <repo>-<lang>.ir.json
  spine.py merge                                →  model.json
  cluster.py                                    →  clusters.json   (candidate clusterings)

The LLM half (scout reconciliation + per-component descriptions → subsystems.json)
and the render (render.py → view.html) are driven by the discover SKILL, which calls
this script first. Nothing written here is durable — it lives in a transient folder.

Per-language extraction is configured with flags (the extractors stay in their own
language; a Go dev has Go, a TS dev has Node — no wf-imposed toolchain beyond Python).

Usage:
  discover.py --repo PATH --out DIR --name NAME \
      [--go-roots a,b --go-mod go.mod] \
      [--ts-roots src --ts-tsconfig tsconfig.json --ts-exclude 'glob,glob']
"""
import argparse, json, os, subprocess, sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable  # the venv python running this script


def run(cmd, out_path=None, **kw):
    print("· " + " ".join(cmd[:6]) + ("…" if len(cmd) > 6 else ""), file=sys.stderr)
    if out_path:
        with open(out_path, "w") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True, **kw)
    else:
        r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"FAILED ({r.returncode}): {' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--go-roots"); ap.add_argument("--go-mod", default="go.mod")
    ap.add_argument("--ts-roots"); ap.add_argument("--ts-tsconfig"); ap.add_argument("--ts-exclude")
    ap.add_argument("--git-window", type=int, default=4000)
    ap.add_argument("--cochange-min", type=int, default=5)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    irs = []

    if a.go_roots:
        go_bin = os.path.join(TOOLS, "readview-go", "readview")
        if not os.path.exists(go_bin):
            sys.exit(f"Go extractor not built: run `cd {TOOLS}/readview-go && go build -o readview .`")
        ir = os.path.join(a.out, f"{a.name}-go.ir.json")
        run([go_bin, "-repo", a.repo, "-roots", a.go_roots, "-gomod", a.go_mod, "-mode", "ir"], out_path=ir)
        irs.append(ir)

    if a.ts_roots:
        ts_js = os.path.join(TOOLS, "readview-ts", "dist", "extract.js")
        if not os.path.exists(ts_js):
            sys.exit(f"TS extractor not built: run `cd {TOOLS}/readview-ts && npm install && npm run build`")
        cmd = ["node", ts_js, "-repo", a.repo, "-roots", a.ts_roots, "-mode", "ir"]
        if a.ts_tsconfig:
            cmd += ["-tsconfig", a.ts_tsconfig]
        if a.ts_exclude:
            cmd += ["-exclude", a.ts_exclude]
        ir = os.path.join(a.out, f"{a.name}-ts.ir.json")
        run(cmd, out_path=ir)
        irs.append(ir)

    if not irs:
        sys.exit("no languages configured (pass --go-roots and/or --ts-roots)")

    model = os.path.join(a.out, "model.json")
    run([PY, os.path.join(TOOLS, "spine.py"), "merge", *irs, "-o", model, "--title", a.name])

    clusters = os.path.join(a.out, "clusters.json")
    run([PY, os.path.join(TOOLS, "cluster.py"), "--model", model, "--repo", a.repo,
         "--out", clusters, "--git-window", str(a.git_window), "--cochange-min", str(a.cochange_min)])

    n = len(json.load(open(model))["nodes"])
    print(json.dumps({"name": a.name, "components": n, "model": model, "clusters": clusters,
                      "next": "scout reconcile → subsystems.json, then render.py"}, indent=2))


if __name__ == "__main__":
    main()
