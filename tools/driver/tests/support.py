"""Shared test scaffolding for the driver test suite.

Puts ``tools/driver`` (and its sibling ``tools/cli``, which the driver's config
reader imports) on ``sys.path``, and builds throwaway projects: a ``.wf/config.yaml``
with every key the driver reads, and — for the git-facing tests — a real repository
with a bare origin.

wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER_DIR = HERE.parent
TOOLS_DIR = DRIVER_DIR.parent
REPO_ROOT = TOOLS_DIR.parent

if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))


CONFIG_TMPL = """\
version: 1

project:
  name: "scratch"
  target: "claude"
  base_branch: "main"

paths:
  tools: "{tools}"
  transient: ".wf/transient"
  skills: ".claude/skills"
  agents: ".claude/agents"
  discover: ".wf/transient/discover"
  discover_brief: ".wf/transient/discover/brief.md"
  telemetry: ".wf/telemetry/sessions.jsonl"
  capabilities: ".wf/CAPABILITIES.yaml"
  charter: ".wf/charter.md"
  plan: ".wf/plan.md"
  tests: ["."]
  learnings: ".wf/LEARNINGS.yaml"
  wf_learnings: ".wf/wf-learnings.yaml"
  adrs: ".wf/adrs"
  design_slice: ".wf/transient/design-slice.md"
  drain_report: ".wf/transient/drain-report.yaml"
  decision_prep: ".wf/transient/decision-prep.md"
  drill_cache: ".wf/transient/drill-cache"
  sprint: ".wf/transient/sprint.yaml"
  archive: ".wf/archive"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  pipeline_history: ".wf/transient/pipeline-history.yaml"
  current_task: ".wf/transient/current-task.yaml"
  review_ready: ".wf/transient/review-ready.yaml"
  feedback: ".wf/transient/feedback.yaml"
  design_issues: ".wf/transient/design-issues.yaml"

parallel:
  worktree_base: ".wf/transient/worktrees"

driver:
  agent_cmd: '{agent_cmd}'
{agent_cmd_overrides}\
  max_parallel: 2
  max_unmerged_sprints: 3
  stop_file: ".wf/transient/STOP"
  state_file: ".wf/transient/driver-state.yaml"
  review_state_cmd: "{review_state_cmd}"
  agent_timeout_s: 60
  command_timeout_s: 60

limits:
  increments_per_sprint: 4
  tasks_per_increment: 10

commands:
  preflight: ""
  stage_check: "{stage_check}"

hygiene:
  file_warn: 400
  file_error: 800
  charter_max: 120
  plan_max: 60

review:
  passes: [wf-review]
  max_attempts: 3

closeout: [wf-retrospective, adequacy, ship]

orchestrate:
  history_cap: 50

id_counters:
  cap: 0
  learning: 0
  sys_tc: 0
"""


def write_config(root: Path, *, tools=None, agent_cmd='echo "{prompt}"',
                 review_state_cmd="", stage_check="", agent_cmd_overrides=None) -> Path:
    """Write a complete .wf/config.yaml into ``root`` and return its path."""
    (root / ".wf").mkdir(parents=True, exist_ok=True)
    cfg = root / ".wf" / "config.yaml"
    overrides = ""
    if agent_cmd_overrides:
        overrides = "  agent_cmd_overrides:\n" + "".join(
            f"    {role}: '{cmd}'\n" for role, cmd in agent_cmd_overrides.items())
    cfg.write_text(CONFIG_TMPL.format(
        tools=str(tools if tools is not None else TOOLS_DIR),
        agent_cmd=agent_cmd,
        agent_cmd_overrides=overrides,
        review_state_cmd=review_state_cmd,
        stage_check=stage_check,
    ))
    return cfg


def commit_wf(root: Path) -> None:
    """Commit the config and gitignore the transient tree — the shape an installed
    repo has, and what the driver's clean-tree gate assumes."""
    (root / ".gitignore").write_text(".wf/transient/\n.wf/telemetry/\n")
    git(root, "add", ".gitignore", ".wf/config.yaml")
    git(root, "commit", "-q", "-m", "wf: config")


def git(root, *args, check=True):
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, timeout=60)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def init_repo(root: Path, base="main") -> None:
    """A real repo with one commit on ``base`` and a bare origin to push to."""
    git(root, "init", "-q", "-b", base)
    git(root, "config", "user.email", "driver@test")
    git(root, "config", "user.name", "driver test")
    git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("scratch\n")
    git(root, "add", "README.md")
    git(root, "commit", "-q", "-m", "init")
    origin = root.parent / (root.name + "-origin.git")
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, timeout=60)
    git(root, "remote", "add", "origin", str(origin))


class TempProject(unittest.TestCase):
    """Base case: a temp dir per test, cleaned up afterwards."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="wf-driver-test-")
        self.root = Path(self._tmp) / "proj"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def stub_bin(self, name: str, body: str) -> Path:
        """Put an executable stub on PATH for this test (e.g. a fake ``gh``)."""
        bindir = Path(self._tmp) / "bin"
        bindir.mkdir(exist_ok=True)
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
        old = os.environ.get("PATH", "")
        if str(bindir) not in old.split(os.pathsep):
            os.environ["PATH"] = f"{bindir}{os.pathsep}{old}"
            self.addCleanup(os.environ.__setitem__, "PATH", old)
        return path
