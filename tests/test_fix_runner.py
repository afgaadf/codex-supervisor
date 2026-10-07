# -*- coding: utf-8 -*-
"""fix_runner.py 的安全边界测试：计划映射、备份、可逆写入、绝不越界。"""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

import checkers as CK
import fix_runner as FR


def _finding(mid, items):
    return CK._finding(mid, "obsidian", "high", "test", items)


class BuildPlanTest(unittest.TestCase):
    def test_safe_and_manual_are_separated(self):
        data = FR.build_plan(findings=[
            _finding("obsidian.missing_frontmatter", ["a.md"]),
            _finding("obsidian.uncommitted_vault", ["未提交 1 处"]),
            _finding("obsidian.note_no_h1", ["b.md"]),
        ])
        got = {x["id"]: x for x in data["plan"]}
        self.assertTrue(got["obsidian.missing_frontmatter"]["auto_apply"])
        self.assertFalse(got["obsidian.uncommitted_vault"]["auto_apply"])
        self.assertFalse(got["obsidian.note_no_h1"]["auto_apply"])
        self.assertEqual(data["summary"]["frontmatter_files"], 1)
        self.assertEqual(data["summary"]["auto_classes"], 1)
        self.assertEqual(data["summary"]["manual_classes"], 2)

    def test_plan_can_scan_current_vault_without_stale_index(self):
        with tempfile.TemporaryDirectory(prefix="fix_scan_") as d:
            vault = Path(d)
            (vault / "a.md").write_bytes(b"# t\n")
            data = FR.build_plan(vault_dir=vault)
            ids = {x["id"] for x in data["plan"]}
            self.assertIn("obsidian.missing_frontmatter", ids)
            self.assertEqual(data["summary"]["frontmatter_files"], 1)

    def test_packaging_includes_fix_runner(self):
        spec = Path(__file__).resolve().parents[1] / "packaging" / "Supervisor.spec"
        self.assertIn('"fix_runner"', spec.read_text(encoding="utf-8"))


class ApplyTest(unittest.TestCase):
    def _paths(self):
        d = tempfile.TemporaryDirectory(prefix="fix_runner_")
        root = Path(d.name)
        vault = root / "vault"
        vault.mkdir()
        return d, root, vault

    def test_frontmatter_added_with_backup_and_body_preserved(self):
        t, root, vault = self._paths()
        try:
            fp = vault / "a.md"
            original = b"hello\r\nworld\r\n"
            fp.write_bytes(original)
            plan = FR.build_plan(findings=[_finding("obsidian.missing_frontmatter", ["a.md"])])
            r = FR.apply(plan, vault_dir=vault, backup_root=root / "bak", log_path=root / "log.jsonl")
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["changed"]["frontmatter"], 1)
            self.assertEqual(len(r["backed_up"]), 1)
            new = fp.read_bytes()
            self.assertTrue(new.startswith(b"---\r\n"))
            self.assertIn(b'title: "a"', new)
            self.assertTrue(new.endswith(original))
            self.assertEqual(Path(r["backed_up"][0]["bak"]).read_bytes(), original)
            self.assertTrue((root / "log.jsonl").is_file())
        finally:
            t.cleanup()

    def test_gitignore_is_created_but_existing_not_overwritten(self):
        t, root, vault = self._paths()
        try:
            plan = FR.build_plan(findings=[_finding("obsidian.vault_no_gitignore", ["缺"])])
            r = FR.apply(plan, vault_dir=vault, backup_root=root / "bak", log_path=root / "log.jsonl")
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["changed"]["gitignore"], 1)
            p = vault / ".gitignore"
            self.assertIn(".obsidian/cache/", p.read_text(encoding="utf-8"))
            before = p.read_bytes()
            r2 = FR.apply(plan, vault_dir=vault, backup_root=root / "bak2", log_path=root / "log2.jsonl")
            self.assertTrue(r2["ok"], r2)
            self.assertEqual(r2["changed"]["gitignore"], 0)
            self.assertEqual(p.read_bytes(), before)
        finally:
            t.cleanup()

    def test_link_report_does_not_change_link(self):
        t, root, vault = self._paths()
        try:
            fp = vault / "a.md"
            original = b"[[missing-target]]\n"
            fp.write_bytes(original)
            plan = FR.build_plan(findings=[
                _finding("obsidian.broken_link", ["a.md -> [[missing-target]]"])])
            r = FR.apply(plan, vault_dir=vault, backup_root=root / "bak", log_path=root / "log.jsonl")
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["changed"]["link_report"], 1)
            self.assertEqual(fp.read_bytes(), original)
            report = vault / r["link_report"]
            self.assertTrue(report.is_file())
            self.assertIn("missing-target", report.read_text(encoding="utf-8"))
        finally:
            t.cleanup()

    def test_manual_only_does_not_touch_vault(self):
        t, root, vault = self._paths()
        try:
            (vault / "a.md").write_bytes(b"# t\n")
            plan = FR.build_plan(findings=[_finding("obsidian.note_no_h1", ["a.md"])])
            r = FR.apply(plan, vault_dir=vault, backup_root=root / "bak", log_path=root / "log.jsonl")
            self.assertTrue(r["ok"], r)
            self.assertEqual(sum(r["changed"].values()), 0)
            self.assertIsNone(r["backup_dir"])
            self.assertEqual((vault / "a.md").read_bytes(), b"# t\n")
        finally:
            t.cleanup()

    def test_path_traversal_is_rejected(self):
        t, root, vault = self._paths()
        try:
            plan = FR.build_plan(findings=[_finding("obsidian.missing_frontmatter", ["../outside.md"])])
            r = FR.apply(plan, vault_dir=vault, backup_root=root / "bak", log_path=root / "log.jsonl")
            self.assertFalse(r["ok"])
            self.assertTrue(r["errors"])
            self.assertFalse((root / "outside.md").exists())
        finally:
            t.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
