from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.version import APP_VERSION
from infra.license_notices import (
    load_about_notice,
    load_license_notices,
    load_project_license_notice,
    load_third_party_notices,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LicenseNoticesLoadingTests(unittest.TestCase):
    def test_load_third_party_notices_reads_first_existing_candidate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_notice = root / "missing" / "THIRD_PARTY_NOTICES.txt"
            existing_notice = root / "THIRD_PARTY_NOTICES.txt"
            existing_notice.write_text("license notices", encoding="utf-8")

            with patch(
                "infra.license_notices.third_party_notice_candidate_paths",
                return_value=(missing_notice, existing_notice),
            ):
                self.assertEqual("license notices", load_third_party_notices())

    def test_load_third_party_notices_keeps_inventory_and_hides_release_checklist(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            notice_path = Path(temp_dir) / "THIRD_PARTY_NOTICES.txt"
            notice_path.write_text(
                "# Third-Party Notices\n\n"
                "## Scope\n\n"
                "Visible scope.\n\n"
                "## Notice Inventory\n\n"
                "Internal inventory.\n\n"
                "## Release Checklist\n\n"
                "Internal checklist.\n\n"
                "## Sources Checked\n\n"
                "Visible sources.\n",
                encoding="utf-8",
            )

            with patch(
                "infra.license_notices.third_party_notice_candidate_paths",
                return_value=(notice_path,),
            ):
                notices = load_third_party_notices()

        self.assertIn("## Scope", notices)
        self.assertIn("Visible scope.", notices)
        self.assertIn("## Notice Inventory", notices)
        self.assertIn("Internal inventory.", notices)
        self.assertIn("## Sources Checked", notices)
        self.assertIn("Visible sources.", notices)
        self.assertNotIn("## Release Checklist", notices)
        self.assertNotIn("Internal checklist.", notices)

    def test_load_third_party_notices_reports_checked_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing_notice = Path(temp_dir) / "THIRD_PARTY_NOTICES.txt"

            with (
                patch(
                    "infra.license_notices.third_party_notice_candidate_paths",
                    return_value=(missing_notice,),
                ),
                self.assertRaises(FileNotFoundError) as context,
            ):
                load_third_party_notices()

            self.assertIn(str(missing_notice), str(context.exception))

    def test_load_about_notice_reads_first_existing_candidate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_about = root / "missing" / "about.txt"
            existing_about = root / "about.txt"
            existing_about.write_text("about notice text", encoding="utf-8")

            with patch(
                "infra.license_notices.about_notice_candidate_paths",
                return_value=(missing_about, existing_about),
            ):
                self.assertEqual("about notice text", load_about_notice())

    def test_load_about_notice_reports_checked_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing_about = Path(temp_dir) / "about.txt"

            with (
                patch(
                    "infra.license_notices.about_notice_candidate_paths",
                    return_value=(missing_about,),
                ),
                self.assertRaises(FileNotFoundError) as context,
            ):
                load_about_notice()

            self.assertIn(str(missing_about), str(context.exception))

    def test_load_project_license_notice_reads_first_existing_candidate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_license = root / "missing" / "LICENSE"
            existing_license = root / "LICENSE"
            existing_license.write_text("GPL license text", encoding="utf-8")

            with patch(
                "infra.license_notices.project_license_candidate_paths",
                return_value=(missing_license, existing_license),
            ):
                notice = load_project_license_notice()

        self.assertTrue(notice.startswith("# j3AITaskRunner License"))
        self.assertIn("GPL license text", notice)

    def test_load_project_license_notice_reports_checked_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing_license = Path(temp_dir) / "LICENSE"

            with (
                patch(
                    "infra.license_notices.project_license_candidate_paths",
                    return_value=(missing_license,),
                ),
                self.assertRaises(FileNotFoundError) as context,
            ):
                load_project_license_notice()

            self.assertIn(str(missing_license), str(context.exception))

    def test_load_license_notices_includes_project_license_first(self) -> None:
        with (
            patch(
                "infra.license_notices.load_project_license_notice",
                return_value="# j3AITaskRunner License\n\nGPL license text",
            ),
            patch(
                "infra.license_notices.load_third_party_notices",
                return_value="# Third-Party Notices",
            ),
        ):
            notices = load_license_notices()

        self.assertTrue(notices.startswith("# j3AITaskRunner License"))
        self.assertIn("GPL license text", notices)
        self.assertIn("# Third-Party Notices", notices)

    def test_source_third_party_notices_cover_release_license_inventory(self) -> None:
        notices = (PROJECT_ROOT / "THIRD_PARTY_NOTICES.txt").read_text(
            encoding="utf-8"
        )

        for expected_text in (
            "GPL-3.0-or-later",
            "Corresponding Source",
            "tkinterdnd2",
            "tkinterdnd2-universal",
            "tkDnD",
            "PyInstaller bootloader",
            "GPL-3.0 compatibility",
            "OpenSSL 3.x",
            "SQLite",
            "expat",
            "libmpdec",
            "mimalloc",
            "Microsoft Universal CRT",
            "Visual C++ Runtime",
            "app_icon.ico",
            "app_icon.svg",
            "Google Fonts Icons",
            "Material Symbols",
            "ECG waveform",
            "Apache-2.0",
            "APACHE-2.0.txt",
            "fonts.google.com/icons",
        ):
            self.assertIn(expected_text, notices)

    def test_source_about_notice_identifies_release_files(self) -> None:
        notice = (PROJECT_ROOT / "about.txt").read_text(encoding="utf-8")

        for expected_text in (
            "j3AITaskRunner",
            "GPL-3.0-or-later",
            "LICENSE",
            "THIRD_PARTY_NOTICES.txt",
            "release source code",
            "same-directory source ZIP",
        ):
            self.assertIn(expected_text, notice)
        self.assertNotIn("Version:", notice)
        self.assertNotIn(APP_VERSION, notice)


if __name__ == "__main__":
    unittest.main()
