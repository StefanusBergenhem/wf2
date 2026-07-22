#!/usr/bin/env python3
"""Tests for discover.py flag routing — where the spine/cluster outputs land.

Focus: --model-out / --clusters-out route the two output files to explicit paths
(config-owned basenames); without them the outputs default under --out. The
extractors and downstream scripts are stubbed — only the argument plumbing runs.
Run:
  <venv>/bin/python tools/discover/discover_test.py   (exit 0 = all pass)
wf2-source-only — never rendered into an install target."""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import discover  # noqa: E402

PASS = 0
FAIL = 0


def ok(m):
    global PASS
    PASS += 1
    print(f"  ok   - {m}")


def bad(m):
    global FAIL
    FAIL += 1
    print(f"  FAIL - {m}")


def arg_after(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def run_main(argv, tmp, components=1):
    """Run discover.main() with the extractor + subprocess layer stubbed.

    Returns the recorded commands. The stub writes a minimal model.json wherever
    the spine merge is told to (-o) so main()'s final summary read succeeds, and an
    IR carrying `components` entries wherever an extractor writes one.
    """
    calls = []

    def fake_run(cmd, out_path=None, **kw):
        calls.append(cmd)
        if out_path and str(out_path).endswith(".ir.json"):
            with open(out_path, "w") as f:
                json.dump({"components": [{"id": f"c{i}"} for i in range(components)]}, f)
        if any(str(c).endswith("spine.py") for c in cmd):
            with open(arg_after(cmd, "-o"), "w") as f:
                json.dump({"nodes": {}}, f)
        if any(str(c).endswith("cluster.py") for c in cmd):
            with open(arg_after(cmd, "--out"), "w") as f:
                json.dump({}, f)

    # A fake TOOLS dir whose go extractor "exists" — fake_run intercepts execution.
    tools = os.path.join(tmp, "tools")
    os.makedirs(os.path.join(tools, "readview-go"), exist_ok=True)
    Path(tools, "readview-go", "readview").touch()

    orig_tools, orig_run = discover.TOOLS, discover.run
    orig_argv = sys.argv
    try:
        discover.TOOLS, discover.run = tools, fake_run
        sys.argv = ["discover.py"] + argv
        discover.main()
    finally:
        discover.TOOLS, discover.run = orig_tools, orig_run
        sys.argv = orig_argv
    return calls


with tempfile.TemporaryDirectory() as tmp:
    # --- default: outputs land under --out with the historical basenames -----
    out = os.path.join(tmp, "outdir")
    calls = run_main(["--repo", ".", "--out", out, "--name", "t", "--go-roots", "a"], tmp)
    spine_cmd = next(c for c in calls if any(str(x).endswith("spine.py") for x in c))
    cluster_cmd = next(c for c in calls if any(str(x).endswith("cluster.py") for x in c))
    if arg_after(spine_cmd, "-o") == os.path.join(out, "model.json"):
        ok("default model path is <out>/model.json")
    else:
        bad(f"default model path: {arg_after(spine_cmd, '-o')}")
    if arg_after(cluster_cmd, "--out") == os.path.join(out, "clusters.json"):
        ok("default clusters path is <out>/clusters.json")
    else:
        bad(f"default clusters path: {arg_after(cluster_cmd, '--out')}")

    # --- explicit flags route both outputs (and cluster reads the routed model)
    model_out = os.path.join(tmp, "elsewhere", "m.json")
    clusters_out = os.path.join(tmp, "elsewhere", "deeper", "c.json")
    calls = run_main(["--repo", ".", "--out", out, "--name", "t", "--go-roots", "a",
                      "--model-out", model_out, "--clusters-out", clusters_out], tmp)
    spine_cmd = next(c for c in calls if any(str(x).endswith("spine.py") for x in c))
    cluster_cmd = next(c for c in calls if any(str(x).endswith("cluster.py") for x in c))
    if arg_after(spine_cmd, "-o") == model_out:
        ok("--model-out routes the spine output")
    else:
        bad(f"--model-out ignored: {arg_after(spine_cmd, '-o')}")
    if arg_after(cluster_cmd, "--out") == clusters_out:
        ok("--clusters-out routes the cluster output")
    else:
        bad(f"--clusters-out ignored: {arg_after(cluster_cmd, '--out')}")
    if arg_after(cluster_cmd, "--model") == model_out:
        ok("cluster step reads the routed model")
    else:
        bad(f"cluster reads wrong model: {arg_after(cluster_cmd, '--model')}")
    if os.path.isfile(model_out) and os.path.isfile(clusters_out):
        ok("parent dirs of routed outputs are created")
    else:
        bad("routed output parent dirs missing")

    # --- an extractor that matched nothing is a hard exit, not a silent count -
    try:
        run_main(["--repo", ".", "--out", os.path.join(tmp, "empty"), "--name", "t",
                  "--go-roots", "cmd,internal", "--go-mod", "go.mod"], tmp, components=0)
        bad("an empty go IR should exit, not report a count")
    except SystemExit as e:
        if "--go-roots cmd,internal" in str(e) and "--go-mod go.mod" in str(e):
            ok("an empty extractor IR exits, naming the flags that matched nothing")
        else:
            bad(f"empty-IR exit message lacks the flags: {e}")

    # --- ensure_built: build-on-first-use, no-op when already built ----------
    # already built → no build step runs (a failing step here would exit if it ran)
    built = os.path.join(tmp, "already", "artifact")
    os.makedirs(os.path.dirname(built), exist_ok=True)
    Path(built).touch()
    try:
        discover.ensure_built(built, [["false"]], tmp, "hint")
        ok("ensure_built no-ops when the artifact already exists")
    except SystemExit:
        bad("ensure_built rebuilt (and failed) an already-built artifact")

    # missing → the build step that creates the artifact runs, then proceeds
    bdir = os.path.join(tmp, "buildme")
    os.makedirs(bdir, exist_ok=True)
    marker = os.path.join(bdir, "out")
    discover.ensure_built(marker, [["sh", "-c", f"touch '{marker}'"]], bdir, "hint")
    if os.path.exists(marker):
        ok("ensure_built runs the build step when the artifact is missing")
    else:
        bad("ensure_built did not build the missing artifact")

    # a failing build step → hard exit naming the manual hint
    try:
        discover.ensure_built(os.path.join(tmp, "nope"), [["false"]], tmp, "run-me-by-hand")
        bad("ensure_built should exit when a build step fails")
    except SystemExit as e:
        if "run-me-by-hand" in str(e):
            ok("ensure_built exits (with the manual hint) when a build step fails")
        else:
            bad(f"ensure_built exit message missing hint: {e}")

    # build steps 'succeed' but leave no artifact → hard exit naming the hint
    try:
        discover.ensure_built(os.path.join(tmp, "still-missing"), [["true"]], tmp, "build-hint")
        bad("ensure_built should exit when the build produced no artifact")
    except SystemExit as e:
        if "build-hint" in str(e):
            ok("ensure_built exits (with the manual hint) when the build makes no artifact")
        else:
            bad(f"ensure_built exit message missing hint: {e}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
