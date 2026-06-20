from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from ui import resources


class UiResourcePathTests(unittest.TestCase):
    def test_resource_path_uses_project_root_when_not_frozen(self) -> None:
        expected_root = Path(resources.__file__).resolve().parents[1]

        with patch.object(resources.sys, "_MEIPASS", None, create=True):
            resolved = resources.app_icon_png_path()

        self.assertEqual(expected_root / "assets" / "app_icon.png", resolved)

    def test_resource_path_uses_pyinstaller_bundle_root_when_frozen(self) -> None:
        bundle_root = Path(r"C:\Temp\_MEI12345")

        with patch.object(resources.sys, "_MEIPASS", str(bundle_root), create=True):
            resolved = resources.app_icon_ico_path()

        self.assertEqual(bundle_root / "assets" / "app_icon.ico", resolved)


if __name__ == "__main__":
    unittest.main()
