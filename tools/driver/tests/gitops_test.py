#!/usr/bin/env python3
"""Tests for the driver's git operations — branching the stack, worktrees, batch
merges, and the DERIVED stack depth (never a stored counter).
Run: python3 tools/driver/tests/gitops_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import unittest

import support  # noqa: F401

import config as driver_config
import gitops


class GitopsTest(support.TempProject):
    def setUp(self):
        super().setUp()
        support.init_repo(self.root)
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        support.commit_wf(self.root)
        self.git = gitops.Git(self.cfg)

    # ── branches ─────────────────────────────────────────────────────────────

    def test_clean_tree_and_current_branch(self):
        self.assertTrue(self.git.is_clean())
        self.assertEqual(self.git.current_branch(), "main")
        (self.root / "dirty.txt").write_text("x")
        self.assertFalse(self.git.is_clean())

    def test_dirty_paths_names_what_makes_the_tree_dirty(self):
        self.assertEqual(self.git.dirty_paths(), [])
        (self.root / "left-over.txt").write_text("x")
        (self.root / "README.md").write_text("changed\n")
        self.assertEqual(sorted(self.git.dirty_paths()),
                         ["README.md", "left-over.txt"])

    def test_sprint_id_is_minted_from_the_branches_that_exist(self):
        self.assertEqual(self.git.next_sprint_id(), "s1")
        self.git.start_branch("sprint/s1", "main")
        support.git(self.root, "checkout", "-q", "main")
        self.assertEqual(self.git.next_sprint_id(), "s2")

    def test_start_branch_is_idempotent_on_resume(self):
        self.git.start_branch("sprint/s1", "main")
        self.assertEqual(self.git.current_branch(), "sprint/s1")
        self.git.start_branch("sprint/s1", "main")  # resume: check it out, do not recut
        self.assertEqual(self.git.current_branch(), "sprint/s1")

    def test_stack_depth_counts_unmerged_sprint_branches(self):
        self.assertEqual(self.git.stack(), [])
        self.git.start_branch("sprint/s1", "main")
        (self.root / "a.txt").write_text("a")
        support.git(self.root, "add", "a.txt")
        support.git(self.root, "commit", "-q", "-m", "s1 work")
        self.git.start_branch("sprint/s2", "sprint/s1")
        (self.root / "b.txt").write_text("b")
        support.git(self.root, "add", "b.txt")
        support.git(self.root, "commit", "-q", "-m", "s2 work")
        self.assertEqual(self.git.stack(), ["sprint/s1", "sprint/s2"])
        # merging the bottom of the stack into base drops it out of the depth
        support.git(self.root, "checkout", "-q", "main")
        support.git(self.root, "merge", "-q", "--no-ff", "-m", "merge s1", "sprint/s1")
        self.assertEqual(self.git.stack(), ["sprint/s2"])

    def test_stack_ignores_legacy_sprint_branches(self):
        # A repo migrated from the old shape carries sprint/<name> branches whose
        # PRs squash-merged (never ancestors of base). Only the driver's own
        # sprint/s<N> branches are the stack.
        self.git.start_branch("sprint/sprint-20260727-spec-first", "main")
        (self.root / "legacy.txt").write_text("x")
        support.git(self.root, "add", "legacy.txt")
        support.git(self.root, "commit", "-q", "-m", "legacy work")
        support.git(self.root, "checkout", "-q", "main")
        self.assertEqual(self.git.stack(), [])
        self.assertEqual(self.git.stack_tip(), "main")

    def test_stack_tip_and_pr_base_follow_the_stack(self):
        self.assertEqual(self.git.stack_tip(), "main")
        self.assertEqual(self.git.pr_base("sprint/s1"), "main")
        self.git.start_branch("sprint/s1", "main")
        (self.root / "a.txt").write_text("a")
        support.git(self.root, "add", "a.txt")
        support.git(self.root, "commit", "-q", "-m", "s1 work")
        self.assertEqual(self.git.stack_tip(), "sprint/s1")
        self.assertEqual(self.git.pr_base("sprint/s2"), "sprint/s1")

    # ── worktrees ────────────────────────────────────────────────────────────

    def test_worktree_add_and_remove(self):
        self.git.start_branch("sprint/s1", "main")
        wt = self.root / ".wf/transient/worktrees/s1-T1"
        self.git.worktree_add(wt, "task/s1-T1", "sprint/s1")
        self.assertTrue((wt / "README.md").is_file())
        self.git.worktree_remove(wt, "task/s1-T1")
        self.assertFalse(wt.exists())

    def test_worktree_add_recreates_a_stale_worktree(self):
        self.git.start_branch("sprint/s1", "main")
        wt = self.root / ".wf/transient/worktrees/s1-T1"
        self.git.worktree_add(wt, "task/s1-T1", "sprint/s1")
        (wt / "leftover.txt").write_text("stale work")
        # the sprint branch moves on: the worktree's base is stale
        support.git(self.root, "checkout", "-q", "sprint/s1")
        (self.root / "moved.txt").write_text("m")
        support.git(self.root, "add", "moved.txt")
        support.git(self.root, "commit", "-q", "-m", "sprint moved")
        self.git.worktree_add(wt, "task/s1-T1", "sprint/s1")
        self.assertFalse((wt / "leftover.txt").exists())
        self.assertTrue((wt / "moved.txt").is_file())

    def test_worktree_add_reuses_a_current_worktree_but_not_a_crashed_run_s_files(self):
        """A worktree whose base has NOT moved on is kept — recreating it would throw
        away the checkout for nothing. But a re-dispatched build restarts from zero, and
        a crashed prior run leaves files a plain reset never touches: an untracked file
        survives into an attempt:0 dispatch, where the build has no signal it did not
        write it and re-verifies it against source to find out."""
        self.git.start_branch("sprint/s1", "main")
        wt = self.root / ".wf/transient/worktrees/s1-T1"
        self.git.worktree_add(wt, "task/s1-T1", "sprint/s1")
        (wt / "crashed_run.txt").write_text("a prior dispatch's untracked work")
        (wt / "scratch").mkdir()
        (wt / "scratch/more.txt").write_text("and its directory")
        (wt / "README.md").write_text("half-edited tracked file\n")
        self.git.worktree_add(wt, "task/s1-T1", "sprint/s1")
        self.assertTrue((wt / "README.md").is_file())
        self.assertFalse((wt / "crashed_run.txt").exists())
        self.assertFalse((wt / "scratch").exists())
        self.assertNotIn("half-edited", (wt / "README.md").read_text())

    def test_worktree_add_keeps_gitignored_provisioning_when_it_reuses(self):
        """`commands.provision` fills a worktree with gitignored dependency dirs that
        cost real time to rebuild — the clean drops the crashed run's files, not those."""
        (self.root / ".gitignore").write_text("node_modules/\n")
        support.git(self.root, "add", ".gitignore")
        support.git(self.root, "commit", "-q", "-m", "ignore deps")
        self.git.start_branch("sprint/s1", "main")
        wt = self.root / ".wf/transient/worktrees/s1-T1"
        self.git.worktree_add(wt, "task/s1-T1", "sprint/s1")
        (wt / "node_modules").mkdir()
        (wt / "node_modules/dep.js").write_text("provisioned")
        self.git.worktree_add(wt, "task/s1-T1", "sprint/s1")
        self.assertTrue((wt / "node_modules/dep.js").is_file())

    # ── carrying a blocked attempt forward ───────────────────────────────────

    def _blocked_attempt(self, filename="attempt.txt", body="what the block left"):
        """A branch holding a blocked task's commits, with the sprint moved on past it —
        exactly the state the successor's worktree is cut from."""
        self.git.start_branch("sprint/s1", "main")
        old = self.root / ".wf/transient/worktrees/s1-OLD"
        self.git.worktree_add(old, "task/s1-S7-T2", "sprint/s1")
        (old / filename).write_text(body)
        support.git(old, "add", filename)
        support.git(old, "commit", "-q", "-m", "S7-T2: the attempt that blocked")
        self.git.worktree_remove(old)
        support.git(self.root, "checkout", "-q", "sprint/s1")
        (self.root / "merged.txt").write_text("landed since\n")
        support.git(self.root, "add", "merged.txt")
        support.git(self.root, "commit", "-q", "-m", "another task merged")

    def test_a_successors_worktree_carries_the_blocked_branch_and_rebases(self):
        self._blocked_attempt()
        wt = self.root / ".wf/transient/worktrees/s1-S8-T1"
        self.assertTrue(self.git.worktree_from(wt, "task/s1-S8-T1", "task/s1-S7-T2",
                                               "sprint/s1"))
        self.assertEqual((wt / "attempt.txt").read_text(), "what the block left")
        self.assertTrue((wt / "merged.txt").is_file())   # rebased onto the sprint tip

    def test_a_conflicting_rebase_falls_back_instead_of_repairing(self):
        """Carrying the old work is opportunistic: spending a repair dispatch to salvage
        a build that was already rejected three times is bad economics."""
        self._blocked_attempt(filename="README.md", body="the attempt's README\n")
        support.git(self.root, "checkout", "-q", "sprint/s1")
        (self.root / "README.md").write_text("the sprint's README\n")
        support.git(self.root, "add", "README.md")
        support.git(self.root, "commit", "-q", "-m", "sprint touched the same file")
        wt = self.root / ".wf/transient/worktrees/s1-S8-T1"
        self.assertFalse(self.git.worktree_from(wt, "task/s1-S8-T1", "task/s1-S7-T2",
                                                "sprint/s1"))
        self.assertFalse(wt.exists())
        self.assertFalse(self.git.merge_in_progress())
        # and the fresh cut still works afterwards
        self.git.worktree_add(wt, "task/s1-S8-T1", "sprint/s1")
        self.assertEqual((wt / "README.md").read_text(), "the sprint's README\n")

    def test_a_vanished_branch_is_not_carried(self):
        self.git.start_branch("sprint/s1", "main")
        wt = self.root / ".wf/transient/worktrees/s1-S8-T1"
        self.assertFalse(self.git.worktree_from(wt, "task/s1-S8-T1", "task/s1-gone",
                                                "sprint/s1"))

    # ── change detection ─────────────────────────────────────────────────────

    def test_changed_since_names_what_moved(self):
        base = support.git(self.root, "rev-parse", "HEAD")
        (self.root / "backend/zones").mkdir(parents=True)
        (self.root / "backend/zones/patch.go").write_text("package zones\n")
        support.git(self.root, "add", "-A")
        support.git(self.root, "commit", "-q", "-m", "a change")
        self.assertEqual(self.git.changed_since(base), ["backend/zones/patch.go"])
        self.assertEqual(self.git.changed_since(
            support.git(self.root, "rev-parse", "HEAD")), [])

    def test_a_sha_the_repo_does_not_have_reads_as_unknowable(self):
        # fail safe: the drill-cache prune drops a digest it cannot check
        self.assertIsNone(self.git.changed_since("0" * 40))
        self.assertIsNone(self.git.changed_since(""))

    # ── merges ───────────────────────────────────────────────────────────────

    def _task_commit(self, branch, filename, content):
        wt = self.root / ".wf/transient/worktrees" / branch.replace("/", "-")
        self.git.worktree_add(wt, branch, "sprint/s1")
        (wt / filename).write_text(content)
        support.git(wt, "add", filename)
        support.git(wt, "commit", "-q", "-m", f"{branch}: work")
        return wt

    def test_clean_merge_reports_the_merge_commit(self):
        self.git.start_branch("sprint/s1", "main")
        self._task_commit("task/T1", "one.txt", "1\n")
        result = self.git.merge("task/T1", "T1: merge")
        self.assertTrue(result.ok)
        self.assertFalse(result.conflict)
        self.assertTrue(result.sha)
        self.assertTrue((self.root / "one.txt").is_file())

    def _conflict(self):
        self.git.start_branch("sprint/s1", "main")
        self._task_commit("task/T1", "shared.txt", "from T1\n")
        self._task_commit("task/T2", "shared.txt", "from T2\n")
        self.assertTrue(self.git.merge("task/T1", "T1: merge").ok)
        return self.git.merge("task/T2", "T2: merge")

    def test_conflicting_merge_is_detected_named_and_left_in_the_tree(self):
        result = self._conflict()
        self.assertFalse(result.ok)
        self.assertTrue(result.conflict)
        self.assertIn("shared.txt", result.files)
        # the conflict stays in the tree — the repair role resolves it in place
        self.assertTrue(self.git.merge_in_progress())

    def test_a_resolved_conflict_completes_the_merge(self):
        self._conflict()
        (self.root / "shared.txt").write_text("from T1 and T2\n")
        support.git(self.root, "add", "shared.txt")
        support.git(self.root, "commit", "-q", "--no-edit")
        self.assertFalse(self.git.merge_in_progress())
        self.assertTrue(self.git.is_clean())
        self.assertTrue(self.git.is_merged_into("task/T2", "sprint/s1"))

    def test_merge_abort_restores_a_clean_branch(self):
        self._conflict()
        self.git.merge_abort()
        self.assertFalse(self.git.merge_in_progress())
        self.assertTrue(self.git.is_clean())
        self.assertFalse(self.git.is_merged_into("task/T2", "sprint/s1"))

    # ── the remote ───────────────────────────────────────────────────────────

    def test_fetch_teaches_the_stack_about_a_branch_merged_on_the_remote(self):
        # sprint/s1 is shipped; someone merges its PR on the forge. Until the driver
        # fetches, the local base has never heard of it and the stack never drains.
        self.git.start_branch("sprint/s1", "main")
        (self.root / "a.txt").write_text("a")
        support.git(self.root, "add", "a.txt")
        support.git(self.root, "commit", "-q", "-m", "s1 work")
        support.git(self.root, "push", "-q", "-u", "origin", "main")
        support.git(self.root, "push", "-q", "-u", "origin", "sprint/s1")
        self.assertEqual(self.git.stack(), ["sprint/s1"])

        clone = self.root.parent / "reviewer"
        support.git(self.root.parent, "clone", "-q",
                    str(self.root.parent / (self.root.name + "-origin.git")), str(clone))
        support.git(clone, "config", "user.email", "reviewer@test")
        support.git(clone, "config", "user.name", "reviewer")
        support.git(clone, "config", "commit.gpgsign", "false")
        support.git(clone, "checkout", "-q", "-B", "main", "origin/main")
        support.git(clone, "merge", "-q", "--no-ff", "-m", "merge s1", "origin/sprint/s1")
        support.git(clone, "push", "-q", "origin", "main")

        ok, detail = self.git.fetch_base()
        self.assertTrue(ok, detail)
        self.assertEqual(self.git.stack(), [])
        # and the next sprint branches off the remote base, not the stale local one
        self.assertEqual(self.git.stack_tip(), "origin/main")

    def test_an_unreachable_origin_is_tolerated(self):
        support.git(self.root, "remote", "set-url", "origin",
                    str(self.root.parent / "no-such-origin.git"))
        ok, detail = self.git.fetch_base()
        self.assertFalse(ok)
        self.assertTrue(detail)
        self.assertEqual(self.git.stack(), [])   # still answers from local state

    def test_cutting_a_sprint_branch_carries_the_dirty_telemetry_sink_across(self):
        # the driver commits the carried telemetry rows AFTER cutting the branch, which
        # only works if `checkout -b` takes the uncommitted sink with it
        sink = self.root / ".wf/telemetry/sessions.jsonl"
        sink.parent.mkdir(parents=True, exist_ok=True)
        sink.write_text('{"event": "ship"}\n')
        support.git(self.root, "add", "--", ".wf/telemetry/sessions.jsonl")
        support.git(self.root, "commit", "-q", "-m", "telemetry: sink")
        self.git.start_branch("sprint/s1", "main")
        support.git(self.root, "commit", "-q", "--allow-empty", "-m", "s1 work")
        sink.write_text('{"event": "ship"}\n{"event": "stop"}\n')   # rows after the ship

        self.git.start_branch("sprint/s2", "sprint/s1")
        self.assertEqual(self.git.current_branch(), "sprint/s2")
        self.assertEqual(self.git.dirty_paths(), [".wf/telemetry/sessions.jsonl"])
        self.assertIn("stop", sink.read_text())
        self.assertTrue(self.git.commit_paths([".wf/telemetry/sessions.jsonl"], "carry"))
        # the rows landed on s2 and left the already-pushed s1 untouched
        self.assertIn("carry", support.git(self.root, "log", "--oneline", "sprint/s2"))
        self.assertNotIn("carry", support.git(self.root, "log", "--oneline", "sprint/s1"))

    def test_commit_paths_skips_an_empty_stage(self):
        self.git.start_branch("sprint/s1", "main")
        self.assertIsNone(self.git.commit_paths(["README.md"], "nothing changed"))
        (self.root / "README.md").write_text("changed\n")
        self.assertTrue(self.git.commit_paths(["README.md"], "changed"))


if __name__ == "__main__":
    unittest.main()
