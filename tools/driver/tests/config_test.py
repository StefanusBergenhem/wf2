#!/usr/bin/env python3
"""Tests for the driver's config reader — every path and knob resolves from
.wf/config.yaml, and a missing key fails loudly instead of defaulting.
Run: python3 tools/driver/tests/config_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import unittest

import support  # noqa: F401  (sys.path bootstrap)

import config as driver_config


class ConfigTest(support.TempProject):
    def load(self, **kw):
        path = support.write_config(self.root, **kw)
        return driver_config.load(str(path))

    def test_paths_resolve_against_the_project_root(self):
        cfg = self.load()
        self.assertEqual(cfg.root, self.root.resolve())
        self.assertEqual(cfg.path("plan"), (self.root / ".wf/plan.md").resolve())
        self.assertEqual(cfg.rel("current_task"), ".wf/transient/current-task.yaml")

    def test_absolute_tools_path_is_used_as_given(self):
        cfg = self.load()
        self.assertEqual(cfg.cli, support.TOOLS_DIR / "cli" / "wf")

    def test_relative_tools_path_anchors_on_the_root(self):
        cfg = self.load(tools=".wf/tools")
        self.assertEqual(cfg.cli, (self.root / ".wf/tools/cli/wf").resolve())

    def test_driver_knobs_read_from_the_driver_block(self):
        cfg = self.load()
        self.assertEqual(cfg.driver("max_parallel"), 2)
        self.assertEqual(cfg.driver("max_unmerged_sprints"), 3)
        self.assertEqual(cfg.agent_cmd, 'echo "{prompt}"')
        self.assertEqual(cfg.stop_file, (self.root / ".wf/transient/STOP").resolve())
        self.assertEqual(cfg.state_file,
                         (self.root / ".wf/transient/driver-state.yaml").resolve())

    def test_missing_driver_key_dies_rather_than_defaulting(self):
        path = support.write_config(self.root)
        path.write_text(path.read_text().replace("  max_parallel: 2\n", ""))
        cfg = driver_config.load(str(path))
        with self.assertRaises(SystemExit):
            cfg.driver("max_parallel")

    def test_lists_and_commands(self):
        cfg = self.load(stage_check="make e2e")
        self.assertEqual(cfg.review_passes, ["wf-review"])
        self.assertEqual(cfg.max_attempts, 3)
        self.assertEqual(cfg.closeout, ["wf-retrospective", "ship"])
        self.assertEqual(cfg.base_branch, "main")
        self.assertEqual(cfg.command("stage_check"), "make e2e")
        self.assertEqual(cfg.command("preflight"), "")
        self.assertEqual(cfg.limit("increments_per_sprint"), 4)

    def test_role_file_prefers_an_agent_over_a_skill(self):
        (self.root / ".claude/agents").mkdir(parents=True)
        (self.root / ".claude/agents/wf-build.md").write_text("agent\n")
        (self.root / ".claude/skills/wf-build").mkdir(parents=True)
        (self.root / ".claude/skills/wf-build/SKILL.md").write_text("skill\n")
        (self.root / ".claude/skills/wf-designer").mkdir(parents=True)
        (self.root / ".claude/skills/wf-designer/SKILL.md").write_text("skill\n")
        cfg = self.load()
        self.assertEqual(cfg.role_file("wf-build"),
                         (self.root / ".claude/agents/wf-build.md").resolve())
        self.assertEqual(cfg.role_file("wf-designer"),
                         (self.root / ".claude/skills/wf-designer/SKILL.md").resolve())

    def test_role_file_of_an_uninstalled_role_is_none(self):
        cfg = self.load()
        self.assertIsNone(cfg.role_file("wf-nope"))


if __name__ == "__main__":
    unittest.main()
