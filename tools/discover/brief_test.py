#!/usr/bin/env python3
"""Tests for brief.py rendering — dependency adjacency + provenance header.

Focus: each component with outgoing deps gets one compact adjacency line under its
subsystem (fan-out capped with "+N more"), and the header carries the provenance
stamp (git SHA + UTC time) that discover.py embeds in model.json's `meta`. Run:
  <venv>/bin/python tools/discover/brief_test.py   (exit 0 = all pass)
wf2-source-only — never rendered into an install target."""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import brief     # noqa: E402
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


def node(uid, deps=(), loc=10):
    lang, path = uid.split(":", 1)
    return {"uid": uid, "id": path, "name": path.rsplit("/", 1)[-1], "path": path,
            "lang": lang, "loc": loc, "has_doc": True, "has_tests": True,
            "synopsis": "x.", "deps": list(deps)}


def model_of(*ns, meta=None):
    m = {"nodes": {n["uid"]: n for n in ns}}
    if meta is not None:
        m["meta"] = meta
    return m


def sub_of(*members):
    return {"subsystems": [{"name": "Core", "members": list(members)}]}


# --- adjacency: a component with deps renders one compact line --------------
m = model_of(node("go:a", deps=["go:b", "go:c"]), node("go:b"), node("go:c"))
text = brief.render_brief(m, sub_of("go:a", "go:b", "go:c"), "t")
if "- `a` → `b`, `c`" in text:
    ok("component with deps renders one adjacency line")
else:
    bad(f"adjacency line missing:\n{text}")

# --- adjacency: a component with no deps renders no line --------------------
if "- `b` →" not in text and "- `c` →" not in text:
    ok("component without deps renders no adjacency line")
else:
    bad("adjacency line rendered for a dep-less component")

# --- adjacency: deps outside the graph are dropped ---------------------------
m = model_of(node("go:a", deps=["go:gone"]))
text = brief.render_brief(m, sub_of("go:a"), "t")
if "- `a` →" not in text:
    ok("deps pointing outside the graph are dropped")
else:
    bad("dangling dep rendered")

# --- adjacency: fan-out past the cap summarizes ------------------------------
fans = [node(f"go:d{i}") for i in range(10)]
m = model_of(node("go:hub", deps=[n["uid"] for n in fans]), *fans)
text = brief.render_brief(m, sub_of("go:hub", *[n["uid"] for n in fans]), "t")
hub_line = next((l for l in text.splitlines() if l.startswith("- `hub` →")), "")
if "+2 more" in hub_line and hub_line.count("`d") == 8:
    ok("fan-out past the cap renders 8 deps + '+N more'")
else:
    bad(f"fan-out not capped: {hub_line!r}")

# --- provenance: meta renders in the header -----------------------------------
m = model_of(node("go:a"), meta={"source_sha": "abc1234",
                                 "generated_at": "2026-07-04T00:00:00Z"})
text = brief.render_brief(m, sub_of("go:a"), "t")
head = text.split("## Subsystems")[0]
if "abc1234" in head and "2026-07-04T00:00:00Z" in head:
    ok("provenance stamp renders in the header")
else:
    bad(f"provenance missing from header:\n{head}")

# --- provenance: no meta, no stamp --------------------------------------------
m = model_of(node("go:a"))
text = brief.render_brief(m, sub_of("go:a"), "t")
if "abc1234" not in text and "source `" not in text:
    ok("no meta renders no provenance line")
else:
    bad("provenance rendered without meta")

# --- stamp: discover.stamp_model embeds sha + UTC time in a git checkout -----
with tempfile.TemporaryDirectory() as tmp:
    repo = os.path.join(tmp, "r")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    Path(repo, "f.txt").write_text("x")
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "x"], check=True)
    sha = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    mp = os.path.join(tmp, "model.json")
    json.dump({"nodes": {}}, open(mp, "w"))
    discover.stamp_model(mp, repo)
    meta = json.load(open(mp)).get("meta", {})
    if meta.get("source_sha") == sha and \
            re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", meta.get("generated_at", "")):
        ok("stamp_model embeds short sha + UTC time in a git checkout")
    else:
        bad(f"bad meta in git repo: {meta}")

    # --- stamp: non-git repo omits the sha gracefully ------------------------
    nongit = os.path.join(tmp, "n")
    os.makedirs(nongit)
    mp2 = os.path.join(tmp, "model2.json")
    json.dump({"nodes": {}}, open(mp2, "w"))
    discover.stamp_model(mp2, nongit)
    meta = json.load(open(mp2)).get("meta", {})
    if "source_sha" not in meta and meta.get("generated_at"):
        ok("non-git repo: sha omitted, timestamp kept")
    else:
        bad(f"bad meta in non-git repo: {meta}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
