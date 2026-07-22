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
      [--model-out FILE] [--clusters-out FILE] \
      [--go-roots a,b --go-mod go.mod] \
      [--ts-roots src --ts-tsconfig tsconfig.json --ts-exclude 'glob,glob']

--model-out / --clusters-out route the two output files explicitly (absolute or
repo-relative paths); omitted, they default to model.json / clusters.json under --out.
"""
import argparse, json, os, subprocess, sys
from datetime import datetime, timezone

TOOLS = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable  # the venv python running this script


def stamp_model(model_path, repo):
    """Embed provenance into model.json: meta.generated_at (UTC) always, plus
    meta.source_sha (short git HEAD) when the repo is a git checkout — omitted
    gracefully otherwise. brief.py renders the stamp in its header."""
    meta = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        r = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            meta["source_sha"] = r.stdout.strip()
    except OSError:
        pass
    m = json.load(open(model_path))
    m["meta"] = meta
    with open(model_path, "w") as f:
        json.dump(m, f)


def check_ir(ir_path, lang, flags):
    """An extractor that produced no components did not match the repo — wrong roots,
    or a module/tsconfig path resolved against the wrong directory. Downstream that is
    silent: spine skips the empty IR and the run still reports a plausible component
    count from whatever language DID extract. Fail here, naming the flags to fix."""
    try:
        with open(ir_path) as f:
            n = len(json.load(f).get("components") or [])
    except (OSError, ValueError) as e:
        sys.exit(f"{lang} extractor wrote an unusable IR ({ir_path}): {e}")
    if n == 0:
        sys.exit(f"{lang} extractor found 0 components with `{flags}`.\n"
                 f"  Roots and module/tsconfig paths are resolved relative to --repo — "
                 f"check they match this repo's layout (e.g. backend/go.mod, not go.mod).")
    return n


def ensure_built(marker, build_steps, cwd, hint):
    """Build an extractor on first use. If `marker` (the built artifact) is absent, run
    each command in `build_steps` in `cwd`; a step that fails — or a build that leaves
    `marker` still absent — is a hard exit naming the manual `hint`. A present marker is a
    no-op: the extractor is never rebuilt on a later run. Mirrors preflight's self-bootstrap
    of a gitignored dependency dir, so a fresh install runs discover without a manual build."""
    if os.path.exists(marker):
        return
    print(f"· building extractor: {cwd}", file=sys.stderr)
    for step in build_steps:
        r = subprocess.run(step, cwd=cwd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"extractor build failed ({' '.join(step)}):\n{r.stderr[-2000:]}\n"
                     f"build it by hand: {hint}")
    if not os.path.exists(marker):
        sys.exit(f"extractor build ran but produced no {os.path.basename(marker)}; "
                 f"build it by hand: {hint}")


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
    ap.add_argument("--model-out", help="model.json path (default: <out>/model.json)")
    ap.add_argument("--clusters-out", help="clusters.json path (default: <out>/clusters.json)")
    ap.add_argument("--go-roots"); ap.add_argument("--go-mod", default="go.mod")
    ap.add_argument("--ts-roots"); ap.add_argument("--ts-tsconfig"); ap.add_argument("--ts-exclude")
    ap.add_argument("--git-window", type=int, default=4000)
    ap.add_argument("--cochange-min", type=int, default=5)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    irs = []

    if a.go_roots:
        go_dir = os.path.join(TOOLS, "readview-go")
        go_bin = os.path.join(go_dir, "readview")
        ensure_built(go_bin, [["go", "build", "-o", "readview", "."]], go_dir,
                     f"cd {go_dir} && go build -o readview .")
        ir = os.path.join(a.out, f"{a.name}-go.ir.json")
        run([go_bin, "-repo", a.repo, "-roots", a.go_roots, "-gomod", a.go_mod, "-mode", "ir"], out_path=ir)
        check_ir(ir, "go", f"--go-roots {a.go_roots} --go-mod {a.go_mod}")
        irs.append(ir)

    if a.ts_roots:
        ts_dir = os.path.join(TOOLS, "readview-ts")
        ts_js = os.path.join(ts_dir, "dist", "extract.js")
        ensure_built(ts_js, [["npm", "install"], ["npm", "run", "build"]], ts_dir,
                     f"cd {ts_dir} && npm install && npm run build")
        cmd = ["node", ts_js, "-repo", a.repo, "-roots", a.ts_roots, "-mode", "ir"]
        if a.ts_tsconfig:
            cmd += ["-tsconfig", a.ts_tsconfig]
        if a.ts_exclude:
            cmd += ["-exclude", a.ts_exclude]
        ir = os.path.join(a.out, f"{a.name}-ts.ir.json")
        run(cmd, out_path=ir)
        flags = f"--ts-roots {a.ts_roots}" + (f" --ts-tsconfig {a.ts_tsconfig}" if a.ts_tsconfig else "")
        check_ir(ir, "ts", flags)
        irs.append(ir)

    if not irs:
        sys.exit("no languages configured (pass --go-roots and/or --ts-roots)")

    model = a.model_out or os.path.join(a.out, "model.json")
    os.makedirs(os.path.dirname(os.path.abspath(model)), exist_ok=True)
    run([PY, os.path.join(TOOLS, "spine.py"), "merge", *irs, "-o", model, "--title", a.name])

    clusters = a.clusters_out or os.path.join(a.out, "clusters.json")
    os.makedirs(os.path.dirname(os.path.abspath(clusters)), exist_ok=True)
    run([PY, os.path.join(TOOLS, "cluster.py"), "--model", model, "--repo", a.repo,
         "--out", clusters, "--git-window", str(a.git_window), "--cochange-min", str(a.cochange_min)])

    stamp_model(model, a.repo)
    n = len(json.load(open(model))["nodes"])
    print(json.dumps({"name": a.name, "components": n, "model": model, "clusters": clusters,
                      "next": "scout reconcile → subsystems.json, then render.py"}, indent=2))


if __name__ == "__main__":
    main()
