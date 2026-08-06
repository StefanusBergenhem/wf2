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
  cap="$(grep -o 'CAP-[0-9]*' .wf/CAPABILITIES.yaml | head -1)"
  n=$((10#${cap##*-}))
  cat > .wf/transient/design-slice.md <<MD
# Design-slice — the greeting seam $n

**Serves:** $cap

## Design narrative

The scratch service gains a greeting seam. The flow runs from the entry point into
the greeting module and back out, wired in the composition root, so one end-to-end
path exists to prove against.

## Claimed scope

- **$cap** — this iteration delivers the greeting for one caller end to end; the
  bulk path is knowingly left for a later sprint.

## Increments

### Increment 1 — the greeting seam

Goal: a caller can be greeted. Allocation: the greeting module and its entry point.
Flow: entry point -> greeting module -> caller.
Checkpoint: after this, greeting a caller demonstrably works.

## System test cases

- **SYS-TC-$n:** a caller is greeted end to end
  **Covers:** $cap
  - **Given** a running scratch service
  - **When** a caller asks to be greeted
  - **Then** the greeting comes back

## Supersessions

None.

## Soundness

- Cohesion: the seam sits in one module. Pass.

## Decision log

<!-- Ships in the sprint PR body. -->

- **Assumption** — $cap read as one caller at a time, not the bulk path.
MD
  ;;
wf-tl)
  mkdir -p .wf/transient
  cap="$(grep -o 'CAP-[0-9]*' .wf/CAPABILITIES.yaml | head -1)"
  n=$((10#${cap##*-}))
  cat > .wf/transient/sprint.yaml <<YAML
sprint_id: "$(field sprint_id)"
tasks:
  - id: "T1"
    increment: 1
    title: "the greeting module"
    covers: ["$cap"]
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
    covers: ["$cap"]
    story: |
      This task proves the greeting path end to end, from the entry point through the
      greeting module the previous task built, so the increment's checkpoint is observable
      rather than asserted. Nothing new is designed here; the path is exercised.
    boundaries: |
      Out of scope: new behaviour. Read-only: README.md.
    grounding:
      - "README.md — the scratch repo's only file"
    system_tests: ["SYS-TC-$n"]
YAML
  ;;
wf-build)
  task="$(field task_id)"
  mkdir -p .wf/transient
  printf 'built by %s on %s\n' "$task" "$(git rev-parse --abbrev-ref HEAD)" > "$task.txt"
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
  printf '# Adequacy: %s — adequate\n**Question:** full-promise\n' "$cap" \
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

SECOND_CAP = """\
  - id: "CAP-002"
    statement: "A caller can be greeted by name."
    value: "The scratch repo has a second observable behaviour."
    status: planned
"""


class EndToEndTest(support.TempProject):
    def setUp(self):
        super().setUp()
        support.init_repo(self.root)
        stub = self.root / "agent-stub.sh"
        stub.write_text(AGENT_STUB)
        stub.chmod(0o755)
        # outside the repo: an untracked log in the tree is foreign dirt, and the
        # clean-tree gate would refuse to cut the next sprint's branch
        self.stub_log = self.root.parent / "stub.log"
        self.gh_log = self.root.parent / "gh.log"
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
        # the telemetry sink is TRACKED, exactly as wf-init's scaffold leaves it: rows
        # appended after a commit dirty the working tree, which the loop must survive
        self.cfg.path("telemetry").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("telemetry").write_text("")
        (self.root / ".gitignore").write_text(".wf/transient/\n")
        support.git(self.root, "add", "-A")
        support.git(self.root, "commit", "-q", "-m", "wf: install")
        support.git(self.root, "push", "-q", "-u", "origin", "main")

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

        # the PR body carries the slice's decision log, read before the close archived it
        body = (self.cfg.path("transient") / "pr-body-s1.md").read_text()
        self.assertIn("## Decision log", body)
        self.assertIn("CAP-001 read as one caller at a time", body)
        self.assertNotIn("Ships in the sprint PR body", body)

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
        self.assertTrue(dispatches)
        self.assertTrue(all(r["agent"] == r["role"] and "duration_s" in r
                            and "rc" in r and r["started_at"] <= r["ended_at"]
                            for r in dispatches))
        self.assertEqual({r["sprint"] for r in rows if "sprint" in r}, {"s1"})

        # every task worktree is cleaned up
        self.assertFalse(list(self.cfg.worktree_base.glob("s1-*")))

        # the run narrates itself: a dispatch writes nothing to the terminal, so without
        # this a live run and a hung one look identical
        for expected in ("wf loop starting", "sprint s1", "designing",
                         "increment 1", "sub-layer 1", "closeout",
                         "wf-designer (originate)", "wf-build · T1",
                         "T1 merged", "shipped s1"):
            self.assertIn(expected, done.stdout, done.stdout)
        # verb-level detail is --verbose only
        self.assertNotIn("wf pipeline next", done.stdout)

    def merge_on_the_forge(self, branch):
        """What a human merging the sprint's PR does: the branch lands in the base on
        the remote, and the local repo only learns about it on the next fetch."""
        clone = self.root.parent / f"reviewer-{branch.replace('/', '-')}"
        origin = self.root.parent / (self.root.name + "-origin.git")
        support.git(self.root.parent, "clone", "-q", str(origin), str(clone))
        support.git(clone, "config", "user.email", "reviewer@test")
        support.git(clone, "config", "user.name", "reviewer")
        support.git(clone, "config", "commit.gpgsign", "false")
        support.git(clone, "checkout", "-q", "-B", "main", "origin/main")
        support.git(clone, "merge", "-q", "--no-ff", "-m", f"merge {branch}",
                    f"origin/{branch}")
        support.git(clone, "push", "-q", "origin", "main")
        support.git(clone, "push", "-q", "origin", "--delete", branch)

    def test_two_sprints_stack_and_drain_across_a_forge_merge(self):
        # The reality one sprint never shows: the loop starts sprint N+1 with HEAD still
        # on sprint N's pushed branch and the committed telemetry sink dirty. The rows
        # must land on the NEW branch — on the old one they un-merge an already-merged
        # sprint, strand it on the stack, and point s2's PR at a base that is gone.
        caps = self.cfg.path("capabilities")
        caps.write_text(caps.read_text() + SECOND_CAP)
        support.git(self.root, "add", "-A")
        support.git(self.root, "commit", "-q", "-m", "po: a second capability")
        support.git(self.root, "push", "-q", "origin", "main")

        first = self.run_driver("--once")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(support.git(self.root, "branch", "--show-current"), "sprint/s1")
        # roles appended to the tracked sink after the close committed it
        self.assertEqual(support.git(self.root, "status", "--porcelain"),
                         f"M {self.cfg.rel('telemetry')}")

        self.merge_on_the_forge("sprint/s1")

        second = self.run_driver("--once")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

        # the carried rows landed on s2 and left the merged s1 untouched
        s1_log = support.git(self.root, "log", "--oneline", "sprint/s1")
        s2_log = support.git(self.root, "log", "--oneline", "sprint/s2")
        self.assertIn("carry rows into sprint s2", s2_log)
        self.assertNotIn("carry rows into sprint s2", s1_log)

        # s1 left the stack: it is still exactly what the forge merged (its tip is the
        # merge's second parent) and therefore an ancestor of the fetched base
        support.git(self.root, "merge-base", "--is-ancestor", "sprint/s1", "origin/main")
        self.assertEqual(support.git(self.root, "rev-parse", "sprint/s1"),
                         support.git(self.root, "rev-parse", "origin/main^2"))

        # s2 built its own work and shipped a PR against the base branch, not against
        # the merged-and-deleted s1
        self.assertIn("T1: merge", s2_log)
        self.assertIn("T2: merge", s2_log)
        prs = [line for line in self.gh_log.read_text().splitlines()
               if "pr create" in line]
        self.assertEqual(len(prs), 2)
        self.assertIn("--base main --head sprint/s2", prs[1])
        self.assertIn("s2: Design-slice — the greeting seam 2", prs[1])

        # both capabilities drained — the second sprint served the one s1 left behind
        remaining = self.cfg.path("capabilities").read_text()
        self.assertNotIn("CAP-001", remaining)
        self.assertNotIn("CAP-002", remaining)

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
