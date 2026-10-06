# -*- coding: utf-8 -*-
"""plugins_status 与两个插件的契约测试。"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import plugins_status as PS


class ReportParsingTest(unittest.TestCase):
    def test_missing_report_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(PS, "REPORT_DIR", Path(d)):
                self.assertIsNone(PS.read_report("codex"))

    def test_broken_json_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "codex.json").write_text("{ not json", encoding="utf-8")
            with mock.patch.object(PS, "REPORT_DIR", Path(d)):
                self.assertIsNone(PS.read_report("codex"))

    def test_reads_valid_report(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "obsidian.json").write_text(
                json.dumps({"plugin": "obsidian", "healthy": True}), encoding="utf-8")
            with mock.patch.object(PS, "REPORT_DIR", Path(d)):
                self.assertTrue(PS.read_report("obsidian")["healthy"])

    def test_age_and_human(self):
        now = datetime.now(timezone.utc)
        self.assertIsNone(PS._report_age_seconds({}))
        self.assertIsNone(PS._report_age_seconds({"ts": "not-a-time"}))
        old = (now - timedelta(minutes=5)).isoformat()
        age = PS._report_age_seconds({"ts": old})
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 290)
        self.assertTrue(PS._human_age(10).endswith("秒前"))
        self.assertTrue(PS._human_age(600).endswith("分钟前"))
        self.assertTrue(PS._human_age(7200).endswith("小时前"))
        self.assertTrue(PS._human_age(200000).endswith("天前"))
        self.assertEqual(PS._human_age(None), "未知")


class StatusShapeTest(unittest.TestCase):
    def test_no_report_no_source_is_not_installed(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(PS, "REPORT_DIR", Path(d)), \
                 mock.patch.object(PS, "CODEX_PLUGIN_SRC", Path(d) / "none"), \
                 mock.patch.object(PS, "CODEX_SKILL_DIR", Path(d) / "skill"):
                st = PS.codex_status()
        self.assertEqual(st["state"], "未安装")
        self.assertFalse(st["installed"])
        for key in ("plugin", "name", "note", "version", "last_run", "summary", "checks", "metrics"):
            self.assertIn(key, st)

    def test_healthy_report_is_running(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "codex.json").write_text(json.dumps({
                "plugin": "codex", "version": "0.1.0", "healthy": True,
                "summary": "就绪", "ts": datetime.now(timezone.utc).isoformat(),
                "checks": [], "metrics": {"modes_total": 60}}), encoding="utf-8")
            with mock.patch.object(PS, "REPORT_DIR", Path(d)):
                st = PS.codex_status()
        self.assertEqual(st["state"], "运行中")
        self.assertEqual(st["version"], "0.1.0")
        self.assertEqual(st["metrics"]["modes_total"], 60)

    def test_unhealthy_report_warns(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "obsidian.json").write_text(json.dumps({
                "plugin": "obsidian", "healthy": False, "summary": "有问题",
                "ts": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
            with mock.patch.object(PS, "REPORT_DIR", Path(d)):
                st = PS.obsidian_status(vault_dir=d)
        self.assertEqual(st["state"], "有告警")
        self.assertFalse(st["healthy"])

    def test_describe_keys(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(PS, "REPORT_DIR", Path(d)):
                out = PS.describe(vault_dir=d)
        self.assertEqual(set(out), {"report_dir", "codex", "obsidian"})

    def test_obsidian_installed_detected(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".obsidian" / "plugins" / "codex-supervisor"
            target.mkdir(parents=True)
            (target / "manifest.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(PS, "REPORT_DIR", Path(d)):
                st = PS.obsidian_status(vault_dir=d)
        self.assertTrue(st["installed"])
        self.assertEqual(st["state"], "已安装")


class InstallTest(unittest.TestCase):
    def test_install_copies_declared_files(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as vault:
            for name in PS.OBSIDIAN_PLUGIN_FILES:
                (Path(src) / name).write_text("x", encoding="utf-8")
            with mock.patch.object(PS, "OBSIDIAN_PLUGIN_SRC", Path(src)):
                ok, msg = PS.install_obsidian_plugin(vault_dir=vault)
            self.assertTrue(ok)
            for name in PS.OBSIDIAN_PLUGIN_FILES:
                self.assertTrue((Path(vault) / ".obsidian" / "plugins" / "codex-supervisor" / name).exists())
            self.assertIn("第三方插件", msg)

    def test_install_missing_vault_fails_gracefully(self):
        with tempfile.TemporaryDirectory() as src:
            with mock.patch.object(PS, "OBSIDIAN_PLUGIN_SRC", Path(src)):
                ok, msg = PS.install_obsidian_plugin(vault_dir=str(Path(src) / "nope"))
        self.assertFalse(ok)
        self.assertIn("不存在", msg)


class PluginSourceContractTest(unittest.TestCase):
    """两个插件的源码必须真实存在于仓库里（随包发布的前置条件）。"""

    def test_codex_plugin_files_exist(self):
        for name in ("SKILL.md", "mcp_server.py", "README.md", "install.py"):
            self.assertTrue((PS.CODEX_PLUGIN_SRC / name).exists(), name)
        skill = (PS.CODEX_PLUGIN_SRC / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name:", skill.splitlines()[0] + skill.splitlines()[1])
        self.assertIn("codex-supervisor", skill)

    def test_obsidian_plugin_files_exist(self):
        for name in PS.OBSIDIAN_PLUGIN_FILES + ("versions.json", "README.md"):
            self.assertTrue((PS.OBSIDIAN_PLUGIN_SRC / name).exists(), name)
        manifest = json.loads((PS.OBSIDIAN_PLUGIN_SRC / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "codex-supervisor")
        self.assertTrue(manifest.get("version"))
        self.assertTrue(manifest.get("isDesktopOnly"))

    def test_obsidian_main_js_skips_anchor_links(self):
        js = (PS.OBSIDIAN_PLUGIN_SRC / "main.js").read_text(encoding="utf-8")
        self.assertIn("unresolvedLinks", js)     # 断链必须读 unresolvedLinks
        self.assertIn("resolvedLinks", js)

    def test_no_bom_in_plugin_sources(self):
        for folder in (PS.CODEX_PLUGIN_SRC, PS.OBSIDIAN_PLUGIN_SRC):
            for f in folder.rglob("*"):
                if f.is_file():
                    self.assertFalse(f.read_bytes().startswith(b"\xef\xbb\xbf"), str(f))


if __name__ == "__main__":
    unittest.main()