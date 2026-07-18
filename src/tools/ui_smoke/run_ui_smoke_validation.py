"""Validation helpers for the UI smoke driver."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

from app.runtime import AUTO_COMMIT_PROMPT
from infra.repository import PERSISTENCE_FILE_NAME
from tools.ui_smoke.app_process import SMOKE_TEXT

EXPECTED_ACTION_IDS = (
    "launch_app",
    "open_workspace",
    "toggle_sidebar_collapsed",
    "toggle_sidebar_expanded",
    "open_scheduled_run_dialog",
    "cancel_scheduled_run_dialog",
    "create_session",
    "open_session_ai_settings_dialog",
    "type_prompt",
    "submit_job",
    "verify_job_registered",
    "verify_auto_commit",
    "verify_progress_log",
    "open_prompt_viewer_dialog",
    "start_queue_with_fake_agent",
    "verify_fake_queue_completion",
    "create_preset_session",
    "verify_preset_controls",
    "verify_manual_candidates_ui",
    "open_preset_ai_settings_dialog",
    "open_bulk_import_dialog",
    "verify_bulk_import",
    "open_about_dialog",
    "open_settings_dialog",
    "open_licenses_dialog",
    "save_settings",
)

def _action_items(report: dict[str, object]) -> list[dict[str, object]]:
    actions = report.get("actions", [])
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


def _latest_action(
    report: dict[str, object],
    action_id: str,
) -> dict[str, object] | None:
    for action in reversed(_action_items(report)):
        if action.get("action_id") == action_id:
            return action
    return None


def _validate_report(report: dict[str, object]) -> None:
    if report.get("app_version") in (None, ""):
        _fail("smoke report is missing app_version.")
    if report.get("last_action") in (None, ""):
        _fail("smoke report is missing last_action.")

    actions = report.get("actions")
    if not isinstance(actions, list):
        _fail("smoke report field 'actions' must be a list.")
    action_items = [
        action for action in actions if isinstance(action, dict)
    ]
    if len(action_items) != len(actions):
        _fail("smoke report field 'actions' contains a non-object item.")

    if not report.get("success"):
        message = report.get("user_message") or report.get("error") or "unknown error"
        _fail(
            f"smoke report marked failure at {report.get('last_action')}: {message}"
        )

    action_ids = [item.get("action_id") for item in action_items]
    for expected_action in EXPECTED_ACTION_IDS:
        if expected_action not in action_ids:
            _fail(f"smoke action was not executed: {expected_action}")
        matching_actions = [
            item for item in action_items if item.get("action_id") == expected_action
        ]
        if matching_actions[-1].get("status") != "passed":
            _fail(
                "smoke action did not pass: "
                f"{expected_action}: {matching_actions[-1].get('error')}"
            )

    jobs = report.get("jobs")
    if not isinstance(jobs, list):
        _fail("smoke report field 'jobs' must be a list.")
    prompts = [job.get("prompt") for job in jobs if isinstance(job, dict)]
    if SMOKE_TEXT not in prompts:
        _fail("smoke prompt job was not present in the report.")
    if AUTO_COMMIT_PROMPT not in prompts:
        _fail("auto-commit job was not present in the report.")

    registered_action = _latest_action(report, "verify_job_registered")
    registered_rows = (
        registered_action.get("workspace_tree_rows")
        if isinstance(registered_action, dict)
        else None
    )
    if not isinstance(registered_rows, list) or len(registered_rows) < 2:
        _fail("registered jobs were not verified against workspace task rows.")

    about_dialog = report.get("about_dialog")
    if not isinstance(about_dialog, dict):
        _fail("smoke report field 'about_dialog' must be an object.")
    if not about_dialog.get("exists_before_close") or not about_dialog.get("closed"):
        _fail("about dialog open/close was not observed in the report.")

    settings_dialog = report.get("settings_dialog")
    if not isinstance(settings_dialog, dict):
        _fail("smoke report field 'settings_dialog' must be an object.")
    if not settings_dialog.get("exists_before_save"):
        _fail("settings dialog open was not observed in the report.")
    if not settings_dialog.get("saved") or not settings_dialog.get("closed"):
        _fail("settings dialog save/close was not observed in the report.")
    if settings_dialog.get("runtime_agent_provider") != "codex":
        _fail("settings dialog did not preserve the Codex provider.")
    runtime_executable_paths = settings_dialog.get("runtime_executable_paths")
    if not isinstance(runtime_executable_paths, dict) or not runtime_executable_paths.get(
        "codex"
    ):
        _fail("settings dialog did not preserve the Codex executable path.")

    licenses_dialog = report.get("licenses_dialog")
    if not isinstance(licenses_dialog, dict):
        _fail("smoke report field 'licenses_dialog' must be an object.")
    if not licenses_dialog.get("exists_before_close") or not licenses_dialog.get("closed"):
        _fail("licenses dialog open/close was not observed in the report.")

    scheduled_run_dialog = report.get("scheduled_run_dialog")
    if not isinstance(scheduled_run_dialog, dict):
        _fail("smoke report field 'scheduled_run_dialog' must be an object.")
    if not scheduled_run_dialog.get("save_closed"):
        _fail("scheduled run save dialog close was not observed in the report.")
    if scheduled_run_dialog.get("save_result") in (None, "None", ""):
        _fail("scheduled run save result was not observed in the report.")
    if not scheduled_run_dialog.get("cancel_closed"):
        _fail("scheduled run cancel dialog close was not observed in the report.")

    ai_settings_dialogs = report.get("ai_settings_dialogs")
    if not isinstance(ai_settings_dialogs, dict):
        _fail("smoke report field 'ai_settings_dialogs' must be an object.")
    for dialog_key in ("session", "preset_action"):
        dialog = ai_settings_dialogs.get(dialog_key)
        if not isinstance(dialog, dict):
            _fail(f"AI settings dialog report is missing: {dialog_key}")
        if not dialog.get("exists_before_save"):
            _fail(f"AI settings dialog open was not observed: {dialog_key}")
        if not dialog.get("saved") or not dialog.get("closed"):
            _fail(f"AI settings dialog save/close was not observed: {dialog_key}")

    prompt_viewer_dialog = report.get("prompt_viewer_dialog")
    if not isinstance(prompt_viewer_dialog, dict):
        _fail("smoke report field 'prompt_viewer_dialog' must be an object.")
    if (
        not prompt_viewer_dialog.get("exists_before_close")
        or not prompt_viewer_dialog.get("closed")
    ):
        _fail("prompt viewer dialog open/close was not observed in the report.")
    if prompt_viewer_dialog.get("prompt") != SMOKE_TEXT:
        _fail("prompt viewer dialog did not show the smoke prompt.")

    queue_execution = report.get("queue_execution")
    if not isinstance(queue_execution, dict):
        _fail("smoke report field 'queue_execution' must be an object.")
    completed_prompts = queue_execution.get("completed_prompts")
    if not isinstance(completed_prompts, list):
        _fail("queue execution report is missing completed_prompts.")
    for expected_prompt in (SMOKE_TEXT, AUTO_COMMIT_PROMPT):
        if expected_prompt not in completed_prompts:
            _fail(f"queue execution did not complete prompt: {expected_prompt}")
    if not queue_execution.get("history_contains_response"):
        _fail("queue execution history did not render the fake response.")
    if not queue_execution.get("log_contains_json_event"):
        _fail("queue execution log did not render fake Codex JSONL events.")
    if not queue_execution.get("workspace_tree_completed"):
        _fail("queue execution did not render completed jobs in the workspace task list.")
    queue_tree_rows = queue_execution.get("workspace_tree_rows")
    if not isinstance(queue_tree_rows, list) or len(queue_tree_rows) < 2:
        _fail("queue execution report is missing workspace task rows.")

    bulk_import = report.get("bulk_import")
    if not isinstance(bulk_import, dict):
        _fail("smoke report field 'bulk_import' must be an object.")
    if not bulk_import.get("submitted") or not bulk_import.get("closed"):
        _fail("bulk import dialog submit/close was not observed in the report.")
    if int(bulk_import.get("created_session_count", 0)) < 1:
        _fail("bulk import did not create a session.")
    if int(bulk_import.get("created_job_count", 0)) < 3:
        _fail("bulk import did not create expected jobs.")
    bulk_tree_rows = bulk_import.get("workspace_tree_rows")
    if not isinstance(bulk_tree_rows, list) or len(bulk_tree_rows) < 3:
        _fail("bulk import report is missing workspace task rows.")
    imported_session_ui = bulk_import.get("imported_session_ui")
    if not isinstance(imported_session_ui, dict):
        _fail("bulk import report is missing imported session UI state.")
    if not imported_session_ui.get("active_session_selected"):
        _fail("bulk import did not select the imported session UI.")
    if not imported_session_ui.get("selected_job_id"):
        _fail("bulk import did not select an imported job in the session UI.")
    if not imported_session_ui.get("auto_commit_checked"):
        _fail("bulk import did not preserve the auto-commit checkbox state.")
    if not imported_session_ui.get("prompt_editor_exists"):
        _fail("bulk import did not create a prompt editor for the imported session.")

    preset_session = report.get("preset_session")
    if not isinstance(preset_session, dict):
        _fail("smoke report field 'preset_session' must be an object.")
    if not preset_session.get("language") or not preset_session.get("instruction"):
        _fail("preset session did not load language and instruction options.")
    if not preset_session.get("prefix_editor_exists"):
        _fail("preset prefix editor was not observed in the report.")
    if not preset_session.get("candidates_tab_exists"):
        _fail("preset candidates tab was not observed in the report.")

    manual_candidates = report.get("manual_candidates")
    if not isinstance(manual_candidates, dict):
        _fail("smoke report field 'manual_candidates' must be an object.")
    if manual_candidates.get("continue_button_state") != "normal":
        _fail("manual candidates Continue button did not enable.")

    sidebar = report.get("sidebar")
    if not isinstance(sidebar, dict):
        _fail("smoke report field 'sidebar' must be an object.")
    if not sidebar.get("collapsed_once") or not sidebar.get("expanded"):
        _fail("sidebar collapse/expand was not observed in the report.")

    window = report.get("window")
    if not isinstance(window, dict):
        _fail("smoke report field 'window' must be an object.")
    if not window.get("geometry"):
        _fail("smoke report window diagnostics are missing geometry.")


def _validate_persistence_file(context: dict[str, Path]) -> None:
    data_path = context["storage"] / PERSISTENCE_FILE_NAME
    if not data_path.is_file():
        _fail(f"persistence file was not created: {data_path}")

    payload = json.loads(data_path.read_text(encoding="utf-8"))
    saved_workspaces = payload.get("saved_workspaces", [])
    expected_workspace = str(context["workspace"].resolve())
    actual_workspaces = {
        str(Path(item.get("path", "")).resolve())
        for item in saved_workspaces
        if isinstance(item, dict) and item.get("path")
    }
    if expected_workspace not in actual_workspaces:
        _fail("workspace was not saved to the persistence file.")

    settings = payload.get("settings", {})
    if settings.get("agent_provider") != "codex":
        _fail("persistence settings did not keep the fake Codex provider.")
    executable_paths = settings.get("executable_paths", {})
    expected_fake_agent = str(context["fake_agent"].resolve())
    actual_fake_agent = executable_paths.get("codex")
    if not actual_fake_agent:
        _fail("test Codex executable path was not preserved in persistence.")
    if str(Path(actual_fake_agent).resolve()) != expected_fake_agent:
        _fail(
            "persistence Codex executable path did not match the isolated fake agent."
        )


def _fail(message: str) -> NoReturn:
    raise RuntimeError(message)
