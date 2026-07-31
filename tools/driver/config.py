"""Driver configuration — one reader, no defaults of its own.

Every path, knob, command and limit the loop uses is resolved from
``.wf/config.yaml`` through the CLI's ``common`` module, so a driver and a `wf`
verb can never disagree about where an artifact lives. A key the config does not
carry is a fatal, named error — the config template owns the default value, and a
second copy of it here is exactly the drift the governor forbids.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The CLI package is the one config reader wf2 has. It sits beside this module in
# both layouts that exist (wf2 source: tools/{cli,driver}; install: .wf/tools/{cli,driver}).
# It is APPENDED, never inserted: the two packages carry same-named modules
# (telemetry, main), and the driver's own directory must win its own imports.
_CLI_DIR = Path(__file__).resolve().parent.parent / "cli"
if str(_CLI_DIR) not in sys.path:
    sys.path.append(str(_CLI_DIR))

import common  # noqa: E402


class Config:
    def __init__(self, config_path: str):
        self.config_path = str(Path(config_path).resolve())
        self.doc = common.config_doc(self.config_path)
        self.root = common.project_root(self.config_path)

    # ── paths ────────────────────────────────────────────────────────────────

    def path(self, key: str) -> Path:
        """paths.<key> anchored on the project root. Fatal when unset."""
        return common.resolve_path(self.config_path, key)

    def path_opt(self, key: str):
        """paths.<key>, or None when the config does not carry it."""
        return self.path(key) if self.rel(key) else None

    def rel(self, key: str):
        """The raw paths.<key> string — what a worktree-local artifact is anchored on."""
        return (self.doc.get("paths") or {}).get(key)

    @property
    def tools(self) -> Path:
        return (self.root / self.path_str("tools")).resolve()

    @property
    def cli(self) -> Path:
        """The `wf` CLI launcher the driver shells out to for every verdict."""
        return self.tools / "cli" / "wf"

    def path_str(self, key: str) -> str:
        value = self.rel(key)
        if not value:
            common.die(f"paths.{key} not set in {self.config_path}")
        return str(value)

    def role_file(self, role: str):
        """The installed file a dispatched role reads: an agent-shaped role lives at
        <paths.agents>/<role>.md, a skill-shaped one at <paths.skills>/<role>/SKILL.md.
        None when neither is installed."""
        agent = (self.root / self.path_str("agents") / f"{role}.md").resolve()
        if agent.is_file():
            return agent
        skill = (self.root / self.path_str("skills") / role / "SKILL.md").resolve()
        return skill if skill.is_file() else None

    # ── knobs ────────────────────────────────────────────────────────────────

    def block(self, name: str) -> dict:
        value = self.doc.get(name)
        return value if isinstance(value, dict) else {}

    def driver(self, key: str):
        """driver.<key>. Fatal when unset — the config template owns the value."""
        block = self.block("driver")
        if key not in block or block[key] is None:
            common.die(f"driver.{key} not set in {self.config_path} — add it "
                       f"(see the config template)")
        return block[key]

    def driver_opt(self, key: str, default=None):
        value = self.block("driver").get(key)
        return default if value is None else value

    def limit(self, key: str) -> int:
        block = self.block("limits")
        if key not in block:
            common.die(f"limits.{key} not set in {self.config_path}")
        return int(block[key])

    def command(self, key: str) -> str:
        """commands.<key> — an empty string means "no such gate in this repo"."""
        return str(self.block("commands").get(key) or "")

    @property
    def agent_cmd(self) -> str:
        return str(self.driver("agent_cmd"))

    def agent_cmd_for(self, role: str) -> str:
        """The launch template for one role: its ``driver.agent_cmd_overrides`` entry
        when the config pins one (a per-role model or harness flag), else the shared
        ``driver.agent_cmd``."""
        overrides = self.driver_opt("agent_cmd_overrides") or {}
        if isinstance(overrides, dict) and overrides.get(role):
            return str(overrides[role])
        return self.agent_cmd

    @property
    def stop_file(self) -> Path:
        return (self.root / str(self.driver("stop_file"))).resolve()

    @property
    def state_file(self) -> Path:
        return (self.root / str(self.driver("state_file"))).resolve()

    @property
    def base_branch(self) -> str:
        project = self.block("project")
        if not project.get("base_branch"):
            common.die(f"project.base_branch not set in {self.config_path}")
        return str(project["base_branch"])

    @property
    def review_passes(self) -> list:
        passes = self.block("review").get("passes")
        if not passes:
            common.die(f"review.passes not set in {self.config_path}")
        return [str(p) for p in passes]

    @property
    def max_attempts(self) -> int:
        attempts = self.block("review").get("max_attempts")
        if not attempts:
            common.die(f"review.max_attempts not set in {self.config_path}")
        return int(attempts)

    @property
    def closeout(self) -> list:
        steps = self.doc.get("closeout")
        if not steps:
            common.die(f"closeout not set in {self.config_path}")
        return [str(s) for s in steps]

    @property
    def worktree_base(self) -> Path:
        base = (self.block("parallel").get("worktree_base"))
        if not base:
            common.die(f"parallel.worktree_base not set in {self.config_path}")
        return (self.root / str(base)).resolve()

    @property
    def history_cap(self) -> int:
        return int(self.block("orchestrate").get("history_cap") or 0)


def load(config_path: str) -> Config:
    return Config(config_path)
