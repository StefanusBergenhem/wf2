#!/usr/bin/env python3
"""Tests for drill-cache freshness — DERIVED from git at every stage close, never
judged by eye. Run: python3 tools/driver/tests/drillcache_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import unittest

import support  # noqa: F401

import config as driver_config
import drillcache
import fakes

HEADER = """\
# Drill: how does the zone patch path work?
**Target:** internal/zones   **Date:** 20260808T000000Z   **Confidence:** high
**Taken at:** {sha}
**Targets:** {targets}

## Summary
It works.
"""


class DrillCacheTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        self.cache = self.cfg.path("drill_cache")
        self.cache.mkdir(parents=True, exist_ok=True)

    def digest(self, name, *, sha="abc1234", targets="store/zones.go, handlers/zones.go"):
        path = self.cache / name
        path.write_text(HEADER.format(sha=sha, targets=targets))
        return path

    def rt(self, changed=None):
        return fakes.runtime(self.cfg, git=fakes.FakeGit(changed=changed))

    def test_a_digest_whose_targets_have_not_moved_survives(self):
        kept = self.digest("zone-patch-20260808T000000Z.md")
        rt = self.rt(changed={"abc1234": ["docs/README.md", "store/other.go"]})
        self.assertEqual(drillcache.prune(rt), [])
        self.assertTrue(kept.exists())

    def test_a_digest_whose_target_changed_is_pruned(self):
        stale = self.digest("zone-patch-20260808T000000Z.md")
        rt = self.rt(changed={"abc1234": ["store/zones.go"]})
        self.assertEqual(drillcache.prune(rt), [stale.name])
        self.assertFalse(stale.exists())

    def test_a_target_directory_matches_the_files_under_it(self):
        stale = self.digest("dir-20260808T000000Z.md", targets="backend/internal/zones")
        rt = self.rt(changed={"abc1234": ["backend/internal/zones/patch.go"]})
        self.assertEqual(drillcache.prune(rt), [stale.name])

    def test_a_digest_naming_no_targets_is_stale_by_definition(self):
        # it cannot be checked at all, so it can never be trusted again
        path = self.cache / "no-targets-20260808T000000Z.md"
        path.write_text("# Drill: something\n**Taken at:** abc1234\n\n## Summary\nx\n")
        rt = self.rt(changed={"abc1234": []})
        self.assertEqual(drillcache.prune(rt), [path.name])

    def test_an_unparseable_header_is_pruned_fail_safe(self):
        path = self.cache / "old-shape-20260808T000000Z.md"
        path.write_text("# Drill: written before the header carried a sha\n\n## Summary\nx\n")
        rt = self.rt(changed={"abc1234": []})
        self.assertEqual(drillcache.prune(rt), [path.name])

    def test_a_sha_git_does_not_have_is_pruned_rather_than_trusted(self):
        path = self.digest("rewritten-20260808T000000Z.md", sha="deadbee")
        rt = self.rt(changed={"abc1234": []})     # nothing recorded for deadbee
        self.assertEqual(drillcache.prune(rt), [path.name])

    def test_adequacy_digests_are_never_swept(self):
        """They share the directory but are verdicts, not reads of the tree — and the
        park count is derived by counting them, so a sweep that took them would silently
        reset a capability's road to being parked."""
        verdict = self.cache / "adequacy-CAP-001-full-promise-20260808T000000Z.md"
        verdict.write_text("# Adequacy: CAP-001 — inadequate\n")
        rt = self.rt(changed={"abc1234": ["store/zones.go"]})
        self.assertEqual(drillcache.prune(rt), [])
        self.assertTrue(verdict.exists())

    def test_an_absent_cache_is_not_an_error(self):
        cfg = driver_config.load(str(support.write_config(self.root)))
        for path in self.cache.iterdir():
            path.unlink()
        self.cache.rmdir()
        self.assertEqual(drillcache.prune(fakes.runtime(cfg)), [])


if __name__ == "__main__":
    unittest.main()
