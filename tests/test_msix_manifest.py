# -*- coding: utf-8 -*-
"""MSIX manifest contract tests."""
from __future__ import annotations
import importlib.util
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

MODULE = Path(APP_DIR) / "packaging" / "msix" / "build_msix.py"
SPEC = importlib.util.spec_from_file_location("build_msix", MODULE)
MSIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MSIX)

NS = {
    "f": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
    "uap5": "http://schemas.microsoft.com/appx/manifest/uap/windows10/5",
    "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
}


class MsixManifestContractTest(unittest.TestCase):
    def setUp(self):
        self.root = ET.fromstring(MSIX.render_manifest("CN=Codex Supervisor Dev", "0.2.1"))

    def test_identity_and_full_trust(self):
        ident = self.root.find("f:Identity", NS)
        self.assertEqual(ident.attrib["Name"], "Codex.Supervisor")
        self.assertEqual(ident.attrib["Publisher"], "CN=Codex Supervisor Dev")
        self.assertEqual(ident.attrib["Version"], "0.2.1.0")
        self.assertIsNotNone(self.root.find(".//rescap:Capability[@Name='runFullTrust']", NS))

    def test_startup_task_points_to_background_exe(self):
        ext = self.root.find(".//uap5:Extension", NS)
        self.assertEqual(ext.attrib["Category"], "windows.startupTask")
        self.assertEqual(ext.attrib["Executable"], "Supervisor\\SupervisorButler.exe")
        task = ext.find("uap5:StartupTask", NS)
        self.assertEqual(task.attrib["TaskId"], "SupervisorButler")
        self.assertEqual(task.attrib["Enabled"], "true")

    def test_msix_version_is_four_part(self):
        self.assertEqual(MSIX.msix_version("0.2.1"), "0.2.1.0")
        self.assertEqual(MSIX.msix_version("0.2.1.4"), "0.2.1.4")
        with self.assertRaises(SystemExit):
            MSIX.msix_version("0.2")


if __name__ == "__main__":
    unittest.main(verbosity=2)