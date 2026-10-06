# -*- coding: utf-8 -*-
"""0.7.0 新增检测器：库规属性 / 重复标题 / 缺 H1 / 碎片标签 / 库内备份 / 缺 gitignore / 收件箱只进不出 + 2 条回合级。"""
import tempfile
import unittest
from pathlib import Path

import checkers as CK

NL = chr(10)


def _w(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _fm(**kw):
    lines = ["---"]
    for k, v in kw.items():
        lines.append("%s: %s" % (k, v))
    lines.append("---")
    return NL.join(lines) + NL


GOOD = dict(type="x", title="y", updated="2026-10-07")


def _ids(f):
    return {x["id"] for x in f}


class TurnLevelTest(unittest.TestCase):
    def test_no_evidence_claim(self):
        self.assertIn("codex.no_evidence_claim", _ids(CK.check_turn("", "写着呢", "已核对无误")))

    def test_evidence_present_is_clean(self):
        self.assertNotIn("codex.no_evidence_claim", _ids(CK.check_turn("", "Get-Content a.txt", "已核对无误")))

    def test_destructive_glob(self):
        self.assertIn("codex.destructive_glob", _ids(CK.check_turn("", "Remove-Item * -Recurse -Force", "")))

    def test_normal_delete_not_flagged(self):
        self.assertNotIn("codex.destructive_glob", _ids(CK.check_turn("", "Remove-Item -LiteralPath a.tmp", "")))


class VaultTextTest(unittest.TestCase):
    def test_missing_required_props(self):
        with tempfile.TemporaryDirectory() as d:
            _w(d, "a.md", _fm(title="x") + "# 标题" + NL)
            self.assertIn("obsidian.property_missing_required", _ids(CK.check_vault_text(d)))

    def test_all_props_ok(self):
        with tempfile.TemporaryDirectory() as d:
            _w(d, ".gitignore", "logs/" + NL)
            _w(d, "a.md", _fm(**GOOD) + "# 标题" + NL)
            self.assertNotIn("obsidian.property_missing_required", _ids(CK.check_vault_text(d)))

    def test_duplicate_heading(self):
        with tempfile.TemporaryDirectory() as d:
            _w(d, "a.md", _fm(**GOOD) + "# 来源" + NL + "正文" + NL + "# 来源" + NL)
            self.assertIn("obsidian.heading_duplicate", _ids(CK.check_vault_text(d)))

    def test_note_without_h1(self):
        with tempfile.TemporaryDirectory() as d:
            _w(d, "a.md", _fm(**GOOD) + "## 二级" + NL)
            self.assertIn("obsidian.note_no_h1", _ids(CK.check_vault_text(d)))

    def test_tag_singleton_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            body = "".join("# 唯一%d" % i + NL + "#tag%d" % i + NL for i in range(25))
            _w(d, "a.md", _fm(**GOOD) + body)
            self.assertIn("obsidian.tag_singleton", _ids(CK.check_vault_text(d)))

    def test_backup_dir_in_vault(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "备份").mkdir()
            _w(d, ".gitignore", "x" + NL)
            _w(d, "a.md", _fm(**GOOD) + "# t" + NL)
            self.assertIn("obsidian.backup_dir_in_vault", _ids(CK.check_vault_text(d)))

    def test_vault_no_gitignore(self):
        with tempfile.TemporaryDirectory() as d:
            _w(d, "a.md", _fm(**GOOD) + "# t" + NL)
            self.assertIn("obsidian.vault_no_gitignore", _ids(CK.check_vault_text(d)))

    def test_inbox_never_emptied(self):
        with tempfile.TemporaryDirectory() as d:
            _w(d, ".gitignore", "x" + NL)
            for i in range(25):
                _w(d, "00_Inbox/%d.md" % i, _fm(tags="[收]", **GOOD) + "# t" + NL)
            self.assertIn("obsidian.inbox_never_emptied", _ids(CK.check_vault_text(d)))

    def test_dot_dirs_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            _w(d, ".agents/skills/s/SKILL.md", "# 没 frontmatter" + NL)
            _w(d, ".gitignore", "x" + NL)
            _w(d, "a.md", _fm(**GOOD) + "# t" + NL)
            self.assertNotIn("obsidian.property_missing_required", _ids(CK.check_vault_text(d)))

    def test_bulk_archive_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            _w(d, ".gitignore", "x" + NL)
            _w(d, "a.md", _fm(**GOOD) + "# t" + NL)
            _w(d, "20_附件/原始资料/网页.md", "# 无 fm" + NL)
            self.assertNotIn("obsidian.property_missing_required", _ids(CK.check_vault_text(d)))

    def test_missing_dir_returns_empty(self):
        self.assertEqual(CK.check_vault_text("C:/definitely/not/here"), [])


if __name__ == "__main__":
    unittest.main()
