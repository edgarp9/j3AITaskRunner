"""Preset, import, and settings actions for the UI smoke scenario."""

from __future__ import annotations

import json
from pathlib import Path
import tkinter as tk

from app.runtime import AUTO_COMMIT_PROMPT
from domain import PresetCandidate
from infra.repository import PERSISTENCE_FILE_NAME
import ui.dialogs as dialogs_module
import ui.main_window as main_window_module

from .app_process_shared import (
    BULK_IMPORT_TEXT,
    POLL_INTERVAL_MS,
    UiSmokeFailure,
    _widget_exists,
)


class UiSmokePresetDialogsMixin:
    def _create_preset_session_via_button(self, workspace_tab_id: str) -> None:
        action = self._begin_action(
            "create_preset_session",
            workspace_tab_id=workspace_tab_id,
            trigger="new_preset_button.invoke",
        )
        workspace_view = self.window._workspace_views[workspace_tab_id]
        button = workspace_view.session_action_buttons.get("button_new_preset")
        if button is None:
            raise UiSmokeFailure("New preset button reference is missing.")

        before_ids = {
            session.session_tab_id
            for session in self.window._runtime.list_session_tabs(workspace_tab_id)
        }
        button.invoke()
        self._drain_ui_events()
        after_sessions = self.window._runtime.list_session_tabs(workspace_tab_id)
        created_sessions = [
            session
            for session in after_sessions
            if session.session_tab_id not in before_ids
        ]
        if not created_sessions:
            raise UiSmokeFailure("New preset button did not create a session.")

        preset_tab = created_sessions[-1]
        session_widgets = self.window._get_session_widgets(preset_tab.session_tab_id)
        if session_widgets.prompt_text is not None:
            raise UiSmokeFailure("Preset session unexpectedly created a prompt editor.")
        if session_widgets.candidates_tab_frame is None:
            raise UiSmokeFailure("Preset session did not create a candidates tab.")

        self._preset_session_tab_id = preset_tab.session_tab_id
        self._pass_action(
            action,
            session_tab_id=preset_tab.session_tab_id,
            session_count=len(after_sessions),
        )
        self._preset_controls_action = self._begin_action(
            "verify_preset_controls",
            session_tab_id=preset_tab.session_tab_id,
        )
        self._after(self._wait_for_preset_controls)

    def _wait_for_preset_controls(self) -> None:
        if self._deadline_expired("preset controls"):
            return

        session_tab_id = self._preset_session_tab_id
        action = self._preset_controls_action
        if session_tab_id is None or action is None:
            self._fail(UiSmokeFailure("Preset session tracking is missing."))
            return

        try:
            self._drain_ui_events()
            session_widgets = self.window._get_session_widgets(session_tab_id)
            language_values = self._combobox_values(
                session_widgets.preset_language_combobox
            )
            instruction_values = self._combobox_values(
                session_widgets.preset_instruction_combobox
            )
            priority_values = self._combobox_values(
                session_widgets.preset_work_priority_combobox
            )
            selected_language = self._var_text(session_widgets.preset_language_var)
            selected_instruction = self._var_text(
                session_widgets.preset_instruction_var
            )
            selected_priority = self._var_text(
                session_widgets.preset_work_priority_var
            )

            if (
                language_values
                and instruction_values
                and priority_values
                and selected_language
                and selected_instruction
                and selected_priority
            ):
                self.preset_session = {
                    "session_tab_id": session_tab_id,
                    "language": selected_language,
                    "instruction": selected_instruction,
                    "priority": selected_priority,
                    "language_count": len(language_values),
                    "instruction_count": len(instruction_values),
                    "priority_values": list(priority_values),
                    "prefix_editor_exists": _widget_exists(
                        session_widgets.preset_prompt_prefix_text
                    ),
                    "register_button_state": self._widget_state(
                        session_widgets.preset_register_button
                    ),
                    "candidates_tab_exists": _widget_exists(
                        session_widgets.candidates_tab_frame
                    ),
                }
                self._pass_action(action, **self.preset_session)
                self._verify_manual_candidates_ui(session_tab_id)
                self._open_and_save_ai_settings_dialog(
                    session_widgets.preset_action_ai_settings_button,
                    action_id="open_preset_ai_settings_dialog",
                    dialog_key="preset_action",
                )
                session_tab = self.window._runtime.get_session_tab(session_tab_id)
                self._open_bulk_import_dialog(session_tab.workspace_tab_id)
                return
        except Exception as exc:
            self._fail(exc)
            return

        self._after(self._wait_for_preset_controls)

    def _verify_manual_candidates_ui(self, session_tab_id: str) -> None:
        action = self._begin_action(
            "verify_manual_candidates_ui",
            session_tab_id=session_tab_id,
        )
        candidates = (
            PresetCandidate(
                id="smoke-1",
                title="Smoke candidate",
                priority="high",
                problem="Manual candidate list did not render.",
                risk="Users cannot continue a manual preset flow.",
                impact="Manual preset workflow is blocked.",
                evidence=("ui smoke evidence",),
            ),
        )
        self.window._render_preset_manual_candidates(
            session_tab_id,
            candidates,
            editable=True,
            status_message="Smoke manual candidates",
        )
        session_widgets = self.window._get_session_widgets(session_tab_id)
        if session_widgets.preset_candidates_continue_button is None:
            raise UiSmokeFailure("Manual candidates Continue button is missing.")
        if "smoke-1" not in session_widgets.preset_candidate_check_vars:
            raise UiSmokeFailure("Manual candidate checkbox variable is missing.")
        session_widgets.preset_candidate_check_vars["smoke-1"].set(True)
        self.window._refresh_manual_candidates_continue_button(session_tab_id)
        continue_state = str(
            session_widgets.preset_candidates_continue_button.cget("state")
        )
        if continue_state != "normal":
            raise UiSmokeFailure("Manual candidates Continue button did not enable.")
        self.manual_candidates = {
            "candidate_count": len(session_widgets.preset_candidate_ids),
            "continue_button_state": continue_state,
            "selected": True,
        }
        self._pass_action(action, **self.manual_candidates)

    def _open_bulk_import_dialog(self, workspace_tab_id: str) -> None:
        action = self._begin_action(
            "open_bulk_import_dialog",
            workspace_tab_id=workspace_tab_id,
            trigger="import_button.invoke",
        )
        workspace_view = self.window._workspace_views[workspace_tab_id]
        button = workspace_view.session_action_buttons.get("button_import")
        if button is None:
            raise UiSmokeFailure("Import button reference is missing.")

        original_dialog = main_window_module.BulkPromptImportDialog
        scenario = self

        class SmokeBulkPromptImportDialog(original_dialog):
            def show_modal(self):
                scenario.bulk_import = {
                    "title": self.title(),
                    "exists_before_submit": bool(self.winfo_exists()),
                    "raw_text": BULK_IMPORT_TEXT,
                }

                def invoke_submit() -> None:
                    try:
                        if self._text is None:
                            raise UiSmokeFailure("Bulk import text widget is missing.")
                        self._text.delete("1.0", tk.END)
                        self._text.insert("1.0", BULK_IMPORT_TEXT)
                        self._on_submit()
                    except Exception as exc:
                        scenario.bulk_import["submit_error"] = str(exc)
                        try:
                            self.destroy()
                        finally:
                            scenario._fail(exc)

                self.after(POLL_INTERVAL_MS, invoke_submit)
                result = super().show_modal()
                scenario.bulk_import["closed"] = not _widget_exists(self)
                scenario.bulk_import["submitted"] = result is not None
                if result is not None:
                    scenario.bulk_import["auto_commit_enabled"] = (
                        result.auto_commit_enabled
                    )
                    scenario.bulk_import["step_execution_mode"] = str(
                        result.step_execution_mode
                    )
                return result

        before_session_ids = {
            session.session_tab_id
            for session in self.window._runtime.list_session_tabs(workspace_tab_id)
        }
        before_job_ids = {
            job.job_id for job in self.window._runtime.list_workspace_jobs(workspace_tab_id)
        }
        main_window_module.BulkPromptImportDialog = SmokeBulkPromptImportDialog
        try:
            button.invoke()
            self._drain_ui_events()
        finally:
            main_window_module.BulkPromptImportDialog = original_dialog

        if not self.bulk_import.get("exists_before_submit"):
            raise UiSmokeFailure("Bulk import dialog did not open.")
        if not self.bulk_import.get("submitted") or not self.bulk_import.get("closed"):
            raise UiSmokeFailure("Bulk import dialog did not submit and close.")
        self._pass_action(action, **self.bulk_import)

        self._verify_bulk_import(
            workspace_tab_id,
            before_session_ids=before_session_ids,
            before_job_ids=before_job_ids,
        )
        self._open_and_close_about_dialog()
        self._open_and_save_settings_dialog()
        self._after(self._wait_for_persistence)

    def _verify_bulk_import(
        self,
        workspace_tab_id: str,
        *,
        before_session_ids: set[str],
        before_job_ids: set[str],
    ) -> None:
        action = self._begin_action(
            "verify_bulk_import",
            workspace_tab_id=workspace_tab_id,
        )
        after_sessions = self.window._runtime.list_session_tabs(workspace_tab_id)
        created_session_ids = [
            session.session_tab_id
            for session in after_sessions
            if session.session_tab_id not in before_session_ids
        ]
        after_jobs = self.window._runtime.list_workspace_jobs(workspace_tab_id)
        created_jobs = [
            job for job in after_jobs if job.job_id not in before_job_ids
        ]
        created_prompts = [job.prompt for job in created_jobs]
        for expected_prompt in (
            "ui smoke imported step 1",
            "ui smoke imported step 2",
            AUTO_COMMIT_PROMPT,
        ):
            if expected_prompt not in created_prompts:
                raise UiSmokeFailure(
                    f"Bulk import did not create expected prompt: {expected_prompt}"
                )
        if not created_session_ids:
            raise UiSmokeFailure("Bulk import did not create a session.")

        workspace_tree_rows = self._assert_workspace_task_rows_match_jobs(
            workspace_tab_id,
            created_jobs,
        )
        first_prompt_job = next(
            (
                job
                for job in created_jobs
                if job.prompt == "ui smoke imported step 1"
            ),
            None,
        )
        if first_prompt_job is None:
            raise UiSmokeFailure("Bulk import first prompt job is missing.")
        first_session_id = first_prompt_job.session_tab_id
        workspace_view = self.window._workspace_views[workspace_tab_id]
        session_widgets = self.window._get_session_widgets(first_session_id)
        active_session_selected = (
            str(workspace_view.session_notebook.select()) == str(session_widgets.frame)
        )
        if not active_session_selected:
            raise UiSmokeFailure("Bulk import did not select the imported session.")
        if session_widgets.selected_job_id != first_prompt_job.job_id:
            raise UiSmokeFailure("Bulk import did not select the first imported job.")
        workspace_tree_selection = tuple(workspace_view.workspace_jobs_tree.selection())
        if first_prompt_job.job_id not in workspace_tree_selection:
            raise UiSmokeFailure("Bulk import did not select the first job in the task list.")
        imported_session_ui = {
            "session_tab_id": first_session_id,
            "selected_job_id": session_widgets.selected_job_id,
            "active_session_selected": active_session_selected,
            "workspace_tree_selection": list(workspace_tree_selection),
            "auto_commit_checked": bool(session_widgets.auto_commit_var.get()),
            "prompt_editor_exists": _widget_exists(session_widgets.prompt_text),
            "activity_text": self._var_text(session_widgets.activity_var),
        }

        self.bulk_import.update(
            {
                "created_session_count": len(created_session_ids),
                "created_job_count": len(created_jobs),
                "created_prompts": created_prompts,
                "workspace_tree_rows": workspace_tree_rows,
                "imported_session_ui": imported_session_ui,
            }
        )
        self._pass_action(
            action,
            created_session_count=len(created_session_ids),
            created_job_count=len(created_jobs),
            created_prompts=created_prompts,
            workspace_tree_rows=workspace_tree_rows,
            imported_session_ui=imported_session_ui,
        )

    def _open_and_close_about_dialog(self) -> None:
        action = self._begin_action(
            "open_about_dialog",
            trigger="about_button.invoke",
        )
        original_about_dialog = main_window_module.AboutDialog
        scenario = self

        class SmokeAboutDialog(original_about_dialog):
            def show_modal(self) -> None:
                scenario.about_dialog = {
                    "title": self.title(),
                    "exists_before_close": bool(self.winfo_exists()),
                }
                self.after(POLL_INTERVAL_MS, self.destroy)
                super().show_modal()
                scenario.about_dialog["closed"] = not _widget_exists(self)

        main_window_module.AboutDialog = SmokeAboutDialog
        try:
            about_button = getattr(self.window, "_about_button", None)
            if about_button is None:
                raise UiSmokeFailure("About button reference is missing.")
            about_button.invoke()
        finally:
            main_window_module.AboutDialog = original_about_dialog

        if not self.about_dialog.get("exists_before_close"):
            raise UiSmokeFailure("About dialog did not open.")
        if not self.about_dialog.get("closed"):
            raise UiSmokeFailure("About dialog did not close.")
        self._pass_action(action, **self.about_dialog)

    def _open_and_save_settings_dialog(self) -> None:
        open_action = self._begin_action(
            "open_settings_dialog",
            trigger="settings_button.invoke",
        )
        original_settings_dialog = main_window_module.SettingsDialog
        original_licenses_dialog = dialogs_module.LicenseNoticesDialog
        scenario = self

        class SmokeLicenseNoticesDialog(original_licenses_dialog):
            def show_modal(self) -> None:
                scenario.licenses_dialog = {
                    "title": self.title(),
                    "exists_before_close": bool(self.winfo_exists()),
                }
                self.after(POLL_INTERVAL_MS, self.destroy)
                super().show_modal()
                scenario.licenses_dialog["closed"] = not _widget_exists(self)

        class SmokeSettingsDialog(original_settings_dialog):
            def show_modal(self):
                scenario.settings_dialog = {
                    "title": self.title(),
                    "exists_before_save": bool(self.winfo_exists()),
                }
                scenario._pass_action(open_action, **scenario.settings_dialog)

                def open_licenses_and_save() -> None:
                    license_action = scenario._begin_action(
                        "open_licenses_dialog",
                        trigger="settings_licenses_button.invoke",
                    )
                    save_action = scenario._begin_action(
                        "save_settings",
                        trigger="settings_save_button.invoke",
                    )
                    try:
                        licenses_button = scenario._find_button_by_text(self, "Licenses")
                        if licenses_button is None:
                            raise UiSmokeFailure("Settings licenses button is missing.")
                        licenses_button.invoke()
                        if not scenario.licenses_dialog.get("exists_before_close"):
                            raise UiSmokeFailure("Licenses dialog did not open.")
                        if not scenario.licenses_dialog.get("closed"):
                            raise UiSmokeFailure("Licenses dialog did not close.")
                        scenario._pass_action(
                            license_action,
                            **scenario.licenses_dialog,
                        )

                        save_button = getattr(self, "_save_button", None)
                        if save_button is None:
                            raise UiSmokeFailure("Settings save button reference is missing.")
                        scenario.settings_dialog["save_button_state"] = str(
                            save_button.cget("state")
                        )
                        save_button.invoke()
                        scenario._pass_action(save_action)
                    except Exception as exc:
                        if license_action.get("status") == "started":
                            scenario._mark_action_failed(license_action, exc)
                        scenario._mark_action_failed(save_action, exc)
                        scenario.settings_dialog["save_error"] = str(exc)
                        try:
                            self.destroy()
                        except tk.TclError:
                            pass

                self.after(POLL_INTERVAL_MS, open_licenses_and_save)
                result = super().show_modal()
                scenario.settings_dialog["closed"] = not _widget_exists(self)
                scenario.settings_dialog["saved"] = result is not None
                return result

        main_window_module.SettingsDialog = SmokeSettingsDialog
        dialogs_module.LicenseNoticesDialog = SmokeLicenseNoticesDialog
        try:
            settings_button = getattr(self.window, "_settings_button", None)
            if settings_button is None:
                raise UiSmokeFailure("Settings button reference is missing.")
            settings_button.invoke()
        finally:
            dialogs_module.LicenseNoticesDialog = original_licenses_dialog
            main_window_module.SettingsDialog = original_settings_dialog

        runtime_settings = self.window._runtime.settings
        runtime_executable_paths = dict(runtime_settings.executable_paths)
        self.settings_dialog.update(
            {
                "runtime_agent_provider": runtime_settings.agent_provider,
                "runtime_executable_paths": runtime_executable_paths,
                "runtime_file_logging_enabled": runtime_settings.file_logging_enabled,
                "runtime_ui_language": runtime_settings.ui_language,
                "runtime_queue_mode": runtime_settings.queue_mode,
            }
        )
        if not self.settings_dialog.get("exists_before_save"):
            raise UiSmokeFailure("Settings dialog did not open.")
        if not self.settings_dialog.get("closed"):
            raise UiSmokeFailure("Settings dialog did not close.")
        if not self.settings_dialog.get("saved"):
            raise UiSmokeFailure("Settings dialog save did not produce settings.")
        if runtime_settings.agent_provider != "codex":
            raise UiSmokeFailure("Settings dialog did not keep Codex as the provider.")
        if not runtime_executable_paths.get("codex"):
            raise UiSmokeFailure("Settings dialog did not keep a Codex executable path.")
        if not self.licenses_dialog.get("closed"):
            raise UiSmokeFailure("Licenses dialog open/close was not observed.")

    def _wait_for_persistence(self) -> None:
        if self._deadline_expired("workspace and settings persistence"):
            return

        try:
            self._drain_ui_events()
            if self._persistence_file_contains_expected_data():
                self._finish_success()
                return
        except Exception as exc:
            self._fail(exc)
            return
        self._after(self._wait_for_persistence)

    def _persistence_file_contains_expected_data(self) -> bool:
        data_path = self._storage_data_path()
        if not data_path.is_file():
            return False

        payload = json.loads(data_path.read_text(encoding="utf-8"))
        saved_workspaces = payload.get("saved_workspaces", [])
        expected_path = self._target_workspace_path
        if expected_path is None:
            return False
        actual_paths = {
            str(Path(item.get("path", "")).resolve())
            for item in saved_workspaces
            if isinstance(item, dict) and item.get("path")
        }
        if str(Path(expected_path).resolve()) not in actual_paths:
            return False

        settings = payload.get("settings", {})
        executable_paths = settings.get("executable_paths", {})
        return bool(settings.get("agent_provider")) and "codex" in executable_paths
