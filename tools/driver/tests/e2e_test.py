#!/usr/bin/env python3
"""The acceptance test: one whole sprint through the real `wf` CLI, real git, and a
stubbed agent that leaves the artifacts a real role would leave.

No LLM is involved — ``driver.agent_cmd`` points at a shell script that writes the
slice, the contracts, the build commits, the review approval and the adequacy digest.
Everything else (branching, worktrees, the frontier, the merges, the drain, the PR)
is the driver and the CLI doing their real work.

Run: python3 tools/driver/tests/e2e_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

import support  # noqa: F401

import config as driver_config

AGENT_STUB = r"""#!/usr/bin/env bash
# A stand-in for every wf role: it reads the prompt and leaves the artifacts the
# real role would leave. Routing is on the role file named in the prompt's first line.
set -u
prompt="$1"
log="$WF_STUB_LOG"
role="$(sed -n '1s#.*/\([a-z0-9-]*\)\(\.md\| and follow it\.\)#\1#p' <<<"$prompt" | head -1)"
case "$prompt" in
  *wf-discover*)     role=wf-discover ;;
  *wf-designer*)     role=wf-designer ;;
  *wf-tl*)           role=wf-tl ;;
  *wf-build*)        role=wf-build ;;
  *wf-review*)       role=wf-review ;;
  *wf-adequacy*)     role=wf-adequacy ;;
  *wf-retrospective*) role=wf-retrospective ;;
esac
echo "$role $(pwd)" >> "$log"
field() { sed -n "s/^$1: //p" <<<"$prompt" | head -1; }

case "$role" in
wf-discover)
  mkdir -p .wf/transient/discover
  printf '# Brief\n\nA scratch repo with one README.\n' > .wf/transient/discover/brief.md
  ;;
wf-designer)
  mkdir -p .wf/transient
  cat > .wf/transient/design-slice.md <<'MD'
# Design-slice — the greeting seam

**Serves:** CAP-001

## Design narrative

The scratch service gains a greeting seam. The flow runs from the entry point into
the greeting module and back out, wired in the composition root, so one end-to-end
path exists to prove against.

## Claimed scope

- **CAP-001** — this iteration delivers the greeting for one caller end to end; the
  bulk path is knowingly left for a later sprint.

## Increments

### Increment 1 — the greeting seam

Goal: a caller can be greeted. Allocation: the greeting module and its entry point.
Flow: entry point -> greeting module -> caller.
Checkpoint: after this, greeting a caller demonstrably works.

## System test cases

- **SYS-TC-1:** a caller is greeted end to end
  **Covers:** CAP-001
  - **Given** a running scratch service
  - **When** a caller asks to be greeted
  - **Then** the greeting comes back

## Supersessions

None.

## Soundness

- Cohesion: the seam sits in one module. Pass.
MD
  ;;
wf-tl)
  mkdir -p .wf/transient
  cat > .wf/transient/sprint.yaml <<'YAML'
sprint_id: "s1"
tasks:
  - id: "T1"
    increment: 1
    title: "the greeting module"
    covers: ["CAP-001"]
    story: |
      This task builds the greeting module behind the entry point, so a caller can be
      greeted through one seam instead of through ad-hoc strings scattered across the
      entry point. The change flows from the entry point into the new module and back.
    acceptance:
      - id: AC-1
        criterion: "When a caller is named, the module returns a greeting for that name."
        tests:
          - {level: unit, target: TestGreeting}
    boundaries: |
      Out of scope: the bulk path. Read-only: README.md. The entry point's signature is fixed.
    grounding:
      - "README.md — the scratch repo's only file"
  - id: "T2"
    increment: 1
    title: "the end-to-end greeting"
    depends_on: ["T1"]
    covers: ["CAP-001"]
    story: |
      This task proves the greeting path end to end, from the entry point through the
      greeting module the previous task built, so the increment's checkpoint is observable
      rather than asserted. Nothing new is designed here; the path is exercised.
    boundaries: |
      Out of scope: new behaviour. Read-only: README.md.
    grounding:
      - "README.md — the scratch repo's only file"
    system_tests: ["SYS-TC-1"]
YAML
  ;;
wf-build)
  task="$(field task_id)"
  mkdir -p .wf/transient
  printf 'built by %s\n' "$task" > "$task.txt"
  git add "$task.txt" >/dev/null
  git commit -q -m "$task: build the change" >/dev/null
  printf 'task_id: "%s"\nstatus: ready_for_review\n' "$task" > .wf/transient/review-ready.yaml
  ;;
wf-review)
  task="$(field task_id)"
  git commit -q --allow-empty -m "$task review: approved" >/dev/null
  ;;
wf-adequacy)
  cap="$(field Capability)"
  mkdir -p .wf/transient/drill-cache
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  printf '# Adequacy: %s — adequate\n**Question:** full promise\n' "$cap" \
    > ".wf/transient/drill-cache/adequacy-$cap-full-promise-$stamp.md"
  ;;
esac
exit 0
"""

GH_STUB = """#!/usr/bin/env bash
echo "$@" >> "$WF_GH_LOG"
echo "https://example.test/scratch/pull/1"
exit 0
"""

CAPS = """\
# CAPABILITIES — the durable why.
version: 1
capabilities:
  - id: "CAP-001"
    statement: "A caller can be greeted."
    value: "The scratch repo has one observable behaviour."
    status: planned
"""


class EndToEndTest(support.TempProject):
    def setUp(self):
        super().setUp()
        support.init_repo(self.root)
        stub = self.root / "agent-stub.sh"
        stub.write_text(AGENT_STUB)
        stub.chmod(0o755)
        self.stub_log = self.root / "stub.log"
        self.gh_log = self.root / "gh.log"
        os.environ["WF_STUB_LOG"] = str(self.stub_log)
        os.environ["WF_GH_LOG"] = str(self.gh_log)
        self.stub_bin("gh", GH_STUB)

        cfg_path = support.write_config(self.root, agent_cmd=f'{stub} "{{prompt}}"')
        self.cfg = driver_config.load(str(cfg_path))
        for role in ("wf-discover", "wf-build", "wf-review", "wf-adequacy",
                     "wf-retrospective", "wf-stage-repair"):
            path = self.root / ".claude/agents" / f"{role}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {role}\n")
        for role in ("wf-designer", "wf-tl"):
            path = self.root / ".claude/skills" / role / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {role}\n")
        self.cfg.path("capabilities").write_text(CAPS)
        self.cfg.path("learnings").write_text("version: 1\nlearnings: []\n")
        support.git(self.root, "add", "-A")
        support.git(self.root, "commit", "-q", "-m", "wf: install")
        (self.root / ".gitignore").write_text(".wf/transient/\n.wf/telemetry/\n")
        support.git(self.root, "add", ".gitignore")
        support.git(self.root, "commit", "-q", "-m", "wf: gitignore")

    def run_driver(self, *args):
        return subprocess.run(
            [sys.executable, str(support.DRIVER_DIR / "wf-driver"),
             "--config", str(self.cfg.config_path), *args],
            capture_output=True, text=True, timeout=600, cwd=str(self.root))

    def test_one_sprint_designs_builds_merges_drains_and_ships(self):
        done = self.run_driver("--once")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

        roles = [line.split()[0] for line in self.stub_log.read_text().splitlines()]
        self.assertEqual(roles[:3], ["wf-discover", "wf-designer", "wf-tl"])
        self.assertEqual(roles.count("wf-build"), 2)
        self.assertEqual(roles.count("wf-review"), 2)
        self.assertIn("wf-adequacy", roles)
        self.assertIn("wf-retrospective", roles)

        # the sprint branch carries both tasks' merges
        log = support.git(self.root, "log", "--oneline", "sprint/s1")
        self.assertIn("T1: merge", log)
        self.assertIn("T2: merge", log)
        self.assertEqual(support.git(self.root, "branch", "--show-current"), "sprint/s1")

        # the adequate verdict drained the capability, and the archive kept a copy
        self.assertNotIn("CAP-001", self.cfg.path("capabilities").read_text())
        archived = list(self.cfg.path("archive").rglob("*"))
        self.assertTrue([p for p in archived if "capabilities" in p.name])
        self.assertTrue([p for p in archived if "sprint" in p.name])

        # shipped: one push, one PR against the base branch
        self.assertIn("sprint/s1", support.git(self.root, "branch", "-r"))
        gh_args = self.gh_log.read_text()
        self.assertIn("pr create", gh_args)
        self.assertIn("--base main", gh_args)
        self.assertIn("--head sprint/s1", gh_args)
        self.assertIn("s1: Design-slice — the greeting seam", gh_args)

        # the run state is reset and the driver is ready for the next sprint
        state = self.cfg.state_file.read_text()
        self.assertIn("phase: sprint_start", state)
        self.assertIn("current_phase: idle",
                      self.cfg.path("pipeline_state").read_text())

        # telemetry carries the driver's own event rows, tagged apart from sessions
        rows = [json.loads(line) for line in
                self.cfg.path("telemetry").read_text().splitlines() if line.strip()]
        kinds = {r["kind"] for r in rows}
        self.assertEqual(kinds, {"driver_event"})
        events = [r["event"] for r in rows]
        self.assertIn("sprint_start", events)
        self.assertIn("merge", events)
        self.assertIn("ship", events)
        dispatches = [r for r in rows if r["event"] == "dispatch"]
        self.assertTrue(all("duration_s" in r and "exit_code" in r for r in dispatches))

        # every task worktree is cleaned up
        self.assertFalse(list(self.cfg.worktree_base.glob("s1-*")))

    def test_a_second_invocation_stops_on_work_exhaustion(self):
        self.assertEqual(self.run_driver("--once").returncode, 0)
        again = self.run_driver("--once")
        self.assertEqual(again.returncode, 0)
        self.assertIn("work_exhaustion", again.stdout)

    def test_dry_run_launches_nothing(self):
        done = self.run_driver("--dry-run", "--once")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("[dry-run] dispatch wf-discover", done.stdout)
        self.assertIn("[dry-run] dispatch wf-designer", done.stdout)
        self.assertFalse(self.stub_log.exists())
        self.assertEqual(support.git(self.root, "branch", "--show-current"), "main")


if __name__ == "__main__":
    unittest.main()
