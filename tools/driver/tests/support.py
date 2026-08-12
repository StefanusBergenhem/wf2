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
  discover_model: ".wf/transient/discover/model.json"
  discover_brief: ".wf/transient/discover/brief.md"
  pi_sessions: ".wf/transient/pi-sessions"
  telemetry: ".wf/telemetry/sessions.jsonl"
  repo_state: ".wf/wf-repo-state.yaml"
  capabilities: ".wf/CAPABILITIES.yaml"
  charter: ".wf/charter.md"
  plan: ".wf/plan.md"
  architecture: ".wf/architecture.md"
  tests: ["."]
  learnings: ".wf/LEARNINGS.yaml"
  wf_learnings: ".wf/wf-learnings.yaml"
  adrs: ".wf/adrs"
  stage: ".wf/transient/stage.yaml"
  pr_body: ".wf/transient/pr-body.md"
  drain_report: ".wf/transient/drain-report.yaml"
  decision_prep: ".wf/transient/decision-prep.md"
  drill_cache: ".wf/transient/drill-cache"
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
  max_stages_per_sprint: {max_stages_per_sprint}
  max_unmerged_sprints: 3
  stop_file: ".wf/transient/STOP"
  state_file: ".wf/transient/driver-state.yaml"
  review_state_cmd: "{review_state_cmd}"
  agent_timeout_s: 60
  command_timeout_s: 60
  rate_limit_max_wait_s: 18000

limits:
  tasks_per_stage: 10

commands:
  preflight: ""
  stage_check: "{stage_check}"
  provision: "{provision}"

hygiene:
  file_warn: 400
  file_error: 800
  charter_max: 120
  plan_max: 60
  architecture_max: 150

review:
  passes: [wf-review]
  max_attempts: 3

closeout: [wf-retrospective, ship]

orchestrate:
  history_cap: 50
"""


def write_config(root: Path, *, tools=None, agent_cmd='echo "{prompt}"',
                 review_state_cmd="", stage_check="", agent_cmd_overrides=None,
                 max_stages_per_sprint=4, provision="") -> Path:
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
        provision=provision,
        max_stages_per_sprint=max_stages_per_sprint,
    ))
    # The id high-water marks live beside the config, not in it — wf-init scaffolds this
    # file, so a project the driver runs against always has one.
    (root / ".wf" / "wf-repo-state.yaml").write_text(
        "version: 1\nid_counters:\n  cap: 0\n  learning: 0\n  wf_learning: 0\n"
        "  sys_tc: 0\n  stage: 0\n")
    return cfg


def stage_doc(*, stage=7, serves=("CAP-001",), tasks=None,
              checkpoint="after this stage, a patch round-trips", decisions=None,
              goal="the patch path exists") -> dict:
    """One cut, in the shape ``paths.stage`` carries: the design header the build
    envelope rides on, plus that stage's task contracts. No ``increment``, no
    ``depends_on`` — a stage IS the tasks with no dependency between them."""
    return {
        "stage": stage,
        "serves": list(serves),
        "goal": goal,
        "allocation": [{"component": "internal/zones", "does": "patch one zone"}],
        "flow": "The handler validates, the store writes the named fields.\n",
        "checkpoint": checkpoint,
        "decisions": list(decisions or ["Assumption — a patch never creates a zone"]),
        "tasks": list(tasks if tasks is not None
                      else [task(f"S{stage}-T1", covers=list(serves)[:1])]),
    }


def task(task_id, *, covers=("CAP-001",), system_tests=None) -> dict:
    """One task contract. An e2e task carries its scenarios and NO acceptance — the
    SYS-TC is the acceptance."""
    entry = {"id": task_id, "title": f"{task_id} — the change",
             "covers": list(covers),
             "story": "Build the thing the stage's flow names.\n",
             "boundaries": "Out of scope: everything else.\n",
             "grounding": ["README.md — the scratch repo's only file"]}
    if system_tests:
        entry["system_tests"] = list(system_tests)
    else:
        entry["acceptance"] = [
            {"id": "AC-1", "criterion": "It works.",
             "tests": [{"level": "unit", "target": f"{task_id}_test.go"}]}]
    return entry


def write_stage(cfg, **kw) -> Path:
    """Write a stage artifact to ``paths.stage`` and return its path."""
    import yaml
    path = cfg.path("stage")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(stage_doc(**kw), sort_keys=False,
                                   allow_unicode=True))
    return path


def commit_wf(root: Path) -> None:
    """Commit the config and gitignore the transient tree — the shape an installed repo
    has. Only the transient tree is ignored: `wf-init`'s scaffold creates the telemetry
    sink as a TRACKED, committed file, so rows appended to it dirty the working tree."""
    (root / ".gitignore").write_text(".wf/transient/\n")
    # paths.repo_state is committed too — it is durable state (the id high-water marks),
    # and an untracked one would read as a dirty tree at every clean-tree gate.
    git(root, "add", ".gitignore", ".wf/config.yaml", ".wf/wf-repo-state.yaml")
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
