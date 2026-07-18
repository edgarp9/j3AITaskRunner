from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.runtime import AUTO_COMMIT_PROMPT
from tools.ui_smoke.app_process import SMOKE_TEXT
from tools.ui_smoke.run_ui_smoke import (
    EXPECTED_ACTION_IDS,
    _action_counts,
    _copy_diagnostics,
    _parse_args,
    _timeout_seconds,
    _validate_report,
)


class UiSmokeTimeoutParsingTests(unittest.TestCase):
    def test_timeout_argument_must_be_positive(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--timeout must be greater"):
            _timeout_seconds(0)

    def test_timeout_env_value_is_used(self) -> None:
        with patch.dict(os.environ, {"UI_SMOKE_TIMEOUT_SECONDS": "12.5"}):
            self.assertEqual(12.5, _timeout_seconds(None))

    def test_timeout_env_value_must_be_numeric(self) -> None:
        with patch.dict(os.environ, {"UI_SMOKE_TIMEOUT_SECONDS": "slow"}):
            with self.assertRaisesRegex(
                RuntimeError,
                "Invalid UI_SMOKE_TIMEOUT_SECONDS",
            ):
                _timeout_seconds(None)


class UiSmokeVerboseParsingTests(unittest.TestCase):
    def test_long_verbose_argument_is_supported(self) -> None:
        args = _parse_args(["--verbose"])

        self.assertTrue(args.verbose)

    def test_short_verbose_argument_is_supported(self) -> None:
        args = _parse_args(["-v"])

        self.assertTrue(args.verbose)


class UiSmokeReportValidationTests(unittest.TestCase):
    def test_valid_report_passes(self) -> None:
        _validate_report(_valid_report())

    def test_missing_action_names_the_missing_action(self) -> None:
        report = _valid_report()
        report["actions"] = [
            action
            for action in report["actions"]
            if action["action_id"] != "save_settings"
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            "smoke action was not executed: save_settings",
        ):
            _validate_report(report)

    def test_failure_report_names_last_action_and_user_message(self) -> None:
        report = _valid_report()
        report["success"] = False
        report["last_action"] = "open_workspace"
        report["user_message"] = "workspace failed"

        with self.assertRaisesRegex(
            RuntimeError,
            "smoke report marked failure at open_workspace: workspace failed",
        ):
            _validate_report(report)

    def test_registered_jobs_require_workspace_task_rows(self) -> None:
        report = _valid_report()
        for action in report["actions"]:
            if action["action_id"] == "verify_job_registered":
                action.pop("workspace_tree_rows")

        with self.assertRaisesRegex(
            RuntimeError,
            "registered jobs were not verified against workspace task rows",
        ):
            _validate_report(report)

    def test_bulk_import_requires_selected_session_ui_state(self) -> None:
        report = _valid_report()
        report["bulk_import"].pop("imported_session_ui")

        with self.assertRaisesRegex(
            RuntimeError,
            "bulk import report is missing imported session UI state",
        ):
            _validate_report(report)

    def test_action_counts_support_summary_output(self) -> None:
        report = _valid_report()
        report["actions"] = [
            {"action_id": "passed_action", "status": "passed"},
            {"action_id": "skipped_action", "status": "skipped"},
            {"action_id": "failed_action", "status": "failed"},
        ]

        self.assertEqual(
            {"passed": 1, "skipped": 1, "failed": 1},
            _action_counts(report),
        )


class UiSmokeDiagnosticsCopyTests(unittest.TestCase):
    def test_copy_diagnostics_replaces_latest_artifacts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            temp_root = root / "smoke-temp"
            artifacts_dir = root / "latest"
            (temp_root / "logs").mkdir(parents=True)
            (temp_root / "logs" / "ui-smoke-report.json").write_text(
                "{}",
                encoding="utf-8",
            )
            artifacts_dir.mkdir()
            (artifacts_dir / "old.txt").write_text("old", encoding="utf-8")

            _copy_diagnostics(temp_root, artifacts_dir)

            self.assertTrue(
                (artifacts_dir / "logs" / "ui-smoke-report.json").is_file()
            )
            self.assertFalse((artifacts_dir / "old.txt").exists())


class UiSmokeWrapperContractTests(unittest.TestCase):
    def test_powershell_wrapper_passes_verbose_to_runner(self) -> None:
        script = Path("tools/ui_smoke/run.ps1").read_text(encoding="utf-8")

        self.assertIn("[CmdletBinding()]", script)
        self.assertIn("$VerbosePreference", script)
        self.assertIn('"--verbose"', script)

    def test_shell_wrapper_passes_arguments_to_runner(self) -> None:
        script = Path("tools/ui_smoke/run.sh").read_text(encoding="utf-8")

        self.assertIn('"$@"', script)


def _valid_report() -> dict[str, object]:
    return {
        "app_version": "test",
        "last_action": "save_settings",
        "success": True,
        "actions": _valid_actions(),
        "jobs": [
            {"prompt": SMOKE_TEXT},
            {"prompt": AUTO_COMMIT_PROMPT},
        ],
        "about_dialog": {
            "exists_before_close": True,
            "closed": True,
        },
        "settings_dialog": {
            "exists_before_save": True,
            "saved": True,
            "closed": True,
            "runtime_agent_provider": "codex",
            "runtime_executable_paths": {"codex": "fake-codex"},
        },
        "licenses_dialog": {
            "exists_before_close": True,
            "closed": True,
        },
        "scheduled_run_dialog": {
            "save_closed": True,
            "save_result": "2026-06-28 12:05:00",
            "cancel_closed": True,
        },
        "ai_settings_dialogs": {
            "session": {
                "exists_before_save": True,
                "saved": True,
                "closed": True,
            },
            "preset_action": {
                "exists_before_save": True,
                "saved": True,
                "closed": True,
            },
        },
        "prompt_viewer_dialog": {
            "exists_before_close": True,
            "closed": True,
            "prompt": SMOKE_TEXT,
        },
        "queue_execution": {
            "completed_prompts": [
                SMOKE_TEXT,
                AUTO_COMMIT_PROMPT,
            ],
            "history_contains_response": True,
            "log_contains_json_event": True,
            "workspace_tree_completed": True,
            "workspace_tree_rows": [
                {"job_id": "job-1", "progress": "Completed"},
                {"job_id": "job-2", "progress": "Completed"},
            ],
        },
        "bulk_import": {
            "submitted": True,
            "closed": True,
            "created_session_count": 1,
            "created_job_count": 4,
            "workspace_tree_rows": [
                {"job_id": "job-3"},
                {"job_id": "job-4"},
                {"job_id": "job-5"},
            ],
            "imported_session_ui": {
                "active_session_selected": True,
                "selected_job_id": "job-3",
                "auto_commit_checked": True,
                "prompt_editor_exists": True,
            },
        },
        "preset_session": {
            "language": "Python",
            "instruction": "bug",
            "prefix_editor_exists": True,
            "candidates_tab_exists": True,
        },
        "manual_candidates": {
            "continue_button_state": "normal",
        },
        "sidebar": {
            "collapsed_once": True,
            "expanded": True,
        },
        "window": {
            "geometry": "1100x800+0+0",
        },
    }


def _valid_actions() -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for action_id in EXPECTED_ACTION_IDS:
        action: dict[str, object] = {"action_id": action_id, "status": "passed"}
        if action_id == "verify_job_registered":
            action["workspace_tree_rows"] = [
                {"job_id": "job-1"},
                {"job_id": "job-2"},
            ]
        actions.append(action)
    return actions
