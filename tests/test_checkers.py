# -*- coding: utf-8 -*-
"""checkers.py 契约测试：把 auto=yes 的失败模式变成可复现、可解释的检测。"""
from __future__ import annotations
import os
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import checkers as C  # noqa: E402

FINDING_KEYS = {"id", "area", "severity", "title", "count", "items"}


def _vault_files():
    """最小知识库：每个条目只触发目标检查，尽量互不干扰。"""
    return [
        {"path": "notes/A.md", "name": "A.md", "links": ["B"], "tags": ["x"], "has_frontmatter": True},
        {"path": "notes/B.md", "name": "B.md", "links": [], "tags": ["x"], "has_frontmatter": True},
        {"path": "notes/broken.md", "name": "broken.md", "links": ["不存在的目标"], "tags": ["x"], "has_frontmatter": True},
        {"path": "notes/nofm.md", "name": "nofm.md", "links": ["B"], "tags": ["x"], "has_frontmatter": False},
        {"path": "notes/orphan.md", "name": "orphan.md", "links": [], "tags": ["x"], "has_frontmatter": True},
        {"path": "notes/anchor.md", "name": "anchor.md", "links": ["#t0"], "tags": ["x"], "has_frontmatter": True},
        {"path": "00_Inbox/x.md", "name": "x.md", "links": ["B"], "tags": [], "has_frontmatter": True},
        {"path": "20_附件/pic.png", "name": "pic.png", "links": [], "tags": [], "has_frontmatter": False},
        {"path": "AGENTS.md", "name": "AGENTS.md", "links": [], "tags": [], "has_frontmatter": False},
        {"path": "CLAUDE.md", "name": "CLAUDE.md", "links": [], "tags": [], "has_frontmatter": False},
        {"path": "maintenance_prompt.md", "name": "maintenance_prompt.md", "links": [], "tags": [], "has_frontmatter": False},
    ]


def _by_id(findings):
    return {f["id"]: f for f in findings}


class CheckTurnTest(unittest.TestCase):
    def test_claim_without_action_fake_verification_and_certainty(self):
        ids = {f["id"] for f in C.check_turn(say="已修复并测试通过，肯定没问题")}
        self.assertIn("codex.claim_without_action", ids)
        self.assertIn("codex.fake_verification", ids)
        self.assertIn("codex.unsupported_certainty", ids)

    def test_ignored_tool_error_when_claiming_done(self):
        ids = {f["id"] for f in C.check_turn(actions="Traceback: boom", say="完成")}
        self.assertIn("codex.ignored_tool_error", ids)

    def test_clean_turn_with_test_run_has_no_false_positive(self):
        ids = {f["id"] for f in C.check_turn(
            actions="python -m unittest discover -s tests",
            say="已运行测试，输出符合预期")}
        self.assertNotIn("codex.claim_without_action", ids)
        self.assertNotIn("codex.fake_verification", ids)

    def test_finding_shape_and_area(self):
        findings = C.check_turn(say="已修复并测试通过，肯定没问题")
        self.assertTrue(findings)
        for f in findings:
            self.assertEqual(set(f), FINDING_KEYS)
            self.assertEqual(f["area"], "codex")
            self.assertEqual(f["count"], len(f["items"]))


class CheckVaultTest(unittest.TestCase):
    def _findings(self, checks=None):
        return _by_id(C.check_vault(_vault_files(), checks=checks))

    def test_index_resolve_existing_vs_missing(self):
        idx = C._Index(_vault_files())
        self.assertTrue(idx.resolve("B"))
        self.assertFalse(idx.resolve("不存在的目标"))

    def test_detects_broken_link(self):
        broken = self._findings()["obsidian.broken_link"]["items"]
        self.assertIn("notes/broken.md -> [[不存在的目标]]", broken)

    def test_detects_missing_frontmatter(self):
        miss = self._findings()["obsidian.missing_frontmatter"]["items"]
        self.assertIn("notes/nofm.md", miss)

    def test_detects_orphan_note(self):
        orphans = self._findings()["obsidian.orphan_note"]["items"]
        self.assertIn("notes/orphan.md", orphans)

    def test_detects_inbox_no_triage(self):
        items = self._findings()["obsidian.inbox_no_triage"]["items"]
        self.assertIn("00_Inbox/x.md", items)

    def test_detects_orphan_attachment(self):
        items = self._findings()["obsidian.orphan_attachment"]["items"]
        self.assertIn("20_附件/pic.png", items)

    def test_detects_uncommitted_from_checks_summary(self):
        findings = self._findings(checks={"summary": {"uncommitted": 2}})
        self.assertIn("obsidian.uncommitted_vault", findings)

    def test_anchor_only_link_is_not_broken(self):
        broken = self._findings().get("obsidian.broken_link", {"items": []})["items"]
        self.assertFalse(any("anchor" in str(it) for it in broken),
                         "纯锚点链接 [[#t0]] 不应算断链")

    def test_pinned_tool_files_ignored(self):
        f = self._findings()
        miss = f.get("obsidian.missing_frontmatter", {"items": []})["items"]
        orphans = f.get("obsidian.orphan_note", {"items": []})["items"]
        for name in ("AGENTS.md", "CLAUDE.md", "maintenance_prompt.md"):
            with self.subTest(name=name):
                self.assertNotIn(name, miss)
                self.assertNotIn(name, orphans)


if __name__ == "__main__":
    unittest.main(verbosity=2)
