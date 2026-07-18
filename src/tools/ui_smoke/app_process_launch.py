"""Launch and queue actions for the in-process UI smoke scenario."""

from __future__ import annotations

import tkinter as tk

from app.runtime import AUTO_COMMIT_PROMPT
from app.version import APP_NAME, APP_VERSION
from domain import JobStatus, QueueStatus
from ui.formatters import job_progress_text
import ui.main_window as main_window_module
import ui.main_window_events as main_window_events_module
import ui.main_window_execution_controls as main_window_execution_controls_module
import ui.main_window_preset as main_window_preset_module
import ui.main_window_workspace_actions as main_window_workspace_actions_module
import ui.settings_dialog as settings_dialog_module

from .app_process_shared import (
    POLL_INTERVAL_MS,
    SMOKE_TEXT,
    UiSmokeFailure,
    _stringify_statuses,
    _widget_exists,
)


class UiSmokeLaunchMixin:
    def install_messagebox_guards(self) -> None:
        def fail_dialog(title: object = "", message: object = "", **_kwargs: object) -> None:
            raise UiSmokeFailure(f"Unexpected modal dialog: {title}: {message}")

        def fail_question(title: object = "", message: object = "", **_kwargs: object) -> bool:
            raise UiSmokeFailure(f"Unexpected modal question: {title}: {message}")

        for module in (
            main_window_module,
            main_window_events_module,
            main_window_preset_module,
            main_window_workspace_actions_module,
            settings_dialog_module,
        ):
            module.messagebox.showerror = fail_dialog
            module.messagebox.showwarning = fail_dialog
            module.messagebox.showinfo = fail_dialog
            module.messagebox.askyesno = fail_question

    def install_dialog_patches(self) -> None:
        self._target_workspace_path = self._resolve_target_workspace_path()

        def askdirectory(*_args: object, **_kwargs: object) -> str:
            if self._target_workspace_path is None:
                raise UiSmokeFailure("Smoke workspace path was not prepared.")
            return self._target_workspace_path

        self._patch_attr(
            main_window_module.filedialog,
            "askdirectory",
            askdirectory,
        )
        self._patch_attr(
            main_window_workspace_actions_module.filedialog,
            "askdirectory",
            askdirectory,
        )

    def start(self) -> None:
        self._after(self._run_launch_action)

    def _run_launch_action(self) -> None:
        action = self._begin_action("launch_app")
        try:
            self.window.update_idletasks()
            self._pass_action(
                action,
                app_name=APP_NAME,
                app_version=APP_VERSION,
                geometry=self._window_geometry(),
            )
            self._open_workspace_from_register_button()
        except Exception as exc:
            self._fail(exc)

    def _open_workspace_from_register_button(self) -> None:
        target_workspace_path = self._target_workspace_path
        if target_workspace_path is None:
            raise UiSmokeFailure("Smoke workspace path was not prepared.")

        action = self._begin_action(
            "open_workspace",
            workspace_path=target_workspace_path,
            path_source="filedialog.askdirectory",
            trigger="workspace_register_button.invoke",
        )
        self._open_workspace_action = action
        register_button = getattr(self.window, "_workspace_register_button", None)
        if register_button is None:
            raise UiSmokeFailure("Workspace register button reference is missing.")
        register_button.invoke()
        self._after(self._wait_for_workspace)

    def _wait_for_workspace(self) -> None:
        if self._deadline_expired("workspace registration"):
            return

        try:
            self._drain_ui_events()
            workspace_tab_id = self._workspace_tab_id_for_target_path()
            if workspace_tab_id is not None:
                if self._open_workspace_action is not None:
                    self._pass_action(
                        self._open_workspace_action,
                        workspace_tab_id=workspace_tab_id,
                    )
                self._toggle_sidebar_round_trip()
                self._open_and_cancel_scheduled_run_dialog()
                self._create_session_via_button(workspace_tab_id)
                return
        except Exception as exc:
            self._fail(exc)
            return
        self._after(self._wait_for_workspace)

    def _toggle_sidebar_round_trip(self) -> None:
        button = getattr(self.window, "_sidebar_toggle_button", None)
        if button is None:
            raise UiSmokeFailure("Sidebar toggle button reference is missing.")

        collapse_action = self._begin_action(
            "toggle_sidebar_collapsed",
            trigger="sidebar_toggle_button.invoke",
        )
        button.invoke()
        self.window.update_idletasks()
        collapsed = bool(getattr(self.window, "_sidebar_collapsed", False))
        if not collapsed:
            raise UiSmokeFailure("Sidebar did not collapse after toggle.")
        self._pass_action(
            collapse_action,
            button_text=str(button.cget("text")),
            collapsed=collapsed,
        )

        expand_action = self._begin_action(
            "toggle_sidebar_expanded",
            trigger="sidebar_toggle_button.invoke",
        )
        button.invoke()
        self.window.update_idletasks()
        expanded = not bool(getattr(self.window, "_sidebar_collapsed", False))
        if not expanded:
            raise UiSmokeFailure("Sidebar did not expand after second toggle.")
        self.sidebar = {
            "collapsed_once": collapsed,
            "expanded": expanded,
            "button_text": str(button.cget("text")),
        }
        self._pass_action(
            expand_action,
            button_text=str(button.cget("text")),
            expanded=expanded,
        )

    def _open_and_cancel_scheduled_run_dialog(self) -> None:
        button = getattr(self.window, "_scheduled_run_button", None)
        if button is None:
            raise UiSmokeFailure("Scheduled run button reference is missing.")

        original_dialog = main_window_module.ScheduledRunDialog
        scenario = self

        class SmokeScheduledRunDialog(original_dialog):
            def show_modal(self):
                mode = str(scenario.scheduled_run_dialog.get("mode", "save"))
                scenario.scheduled_run_dialog[f"{mode}_title"] = self.title()
                scenario.scheduled_run_dialog[
                    f"{mode}_exists_before_close"
                ] = bool(self.winfo_exists())

                def complete_dialog() -> None:
                    try:
                        if mode == "cancel":
                            self._on_cancel_schedule()
                        else:
                            self._on_submit()
                    except Exception as exc:
                        scenario.scheduled_run_dialog[f"{mode}_error"] = str(exc)
                        try:
                            self.destroy()
                        finally:
                            scenario._fail(exc)

                self.after(POLL_INTERVAL_MS, complete_dialog)
                result = super().show_modal()
                scenario.scheduled_run_dialog[f"{mode}_closed"] = not _widget_exists(
                    self
                )
                scenario.scheduled_run_dialog[f"{mode}_result"] = (
                    None if result is None else str(result.scheduled_at)
                )
                return result

        main_window_module.ScheduledRunDialog = SmokeScheduledRunDialog
        try:
            open_action = self._begin_action(
                "open_scheduled_run_dialog",
                trigger="scheduled_run_button.invoke",
            )
            self.scheduled_run_dialog["mode"] = "save"
            button.invoke()
            scheduled_at = getattr(self.window, "_scheduled_run_at", None)
            if scheduled_at is None:
                raise UiSmokeFailure("Scheduled run was not saved from the dialog.")
            self._pass_action(open_action, scheduled_at=str(scheduled_at))

            cancel_action = self._begin_action(
                "cancel_scheduled_run_dialog",
                trigger="scheduled_run_button.invoke",
            )
            self.scheduled_run_dialog["mode"] = "cancel"
            button.invoke()
            if getattr(self.window, "_scheduled_run_at", None) is not None:
                raise UiSmokeFailure("Scheduled run was not canceled from the dialog.")
            self._pass_action(cancel_action)
        finally:
            main_window_module.ScheduledRunDialog = original_dialog
            self.scheduled_run_dialog.pop("mode", None)

    def _create_session_via_button(self, workspace_tab_id: str) -> None:
        action = self._begin_action(
            "create_session",
            workspace_tab_id=workspace_tab_id,
            trigger="new_session_button.invoke",
        )
        workspace_view = self.window._workspace_views[workspace_tab_id]
        button = workspace_view.session_action_buttons.get("button_new_session")
        if button is None:
            raise UiSmokeFailure("New session button reference is missing.")

        before_ids = {
            session.session_tab_id
            for session in self.window._runtime.list_session_tabs(workspace_tab_id)
        }
        button.invoke()
        self.window.update_idletasks()
        after_sessions = self.window._runtime.list_session_tabs(workspace_tab_id)
        created_sessions = [
            session
            for session in after_sessions
            if session.session_tab_id not in before_ids
        ]
        if not created_sessions:
            raise UiSmokeFailure("New session button did not create a session.")

        session_tab = created_sessions[-1]
        session_widgets = self.window._get_session_widgets(session_tab.session_tab_id)
        if session_widgets.prompt_text is None:
            raise UiSmokeFailure("Normal session prompt editor was not created.")
        self._session_tab_id = session_tab.session_tab_id
        self._pass_action(
            action,
            session_tab_id=session_tab.session_tab_id,
            session_count=len(after_sessions),
        )
        self._open_and_save_ai_settings_dialog(
            session_widgets.ai_settings_button,
            action_id="open_session_ai_settings_dialog",
            dialog_key="session",
        )
        self._type_prompt_text(session_tab.session_tab_id)

    def _open_and_save_ai_settings_dialog(
        self,
        button: tk.Misc | None,
        *,
        action_id: str,
        dialog_key: str,
    ) -> None:
        if button is None:
            raise UiSmokeFailure("AI settings button reference is missing.")
        if str(button.cget("state")) == "disabled":
            raise UiSmokeFailure(f"AI settings button is disabled for {dialog_key}.")

        action = self._begin_action(action_id, trigger="ai_settings_button.invoke")
        original_dialog = main_window_execution_controls_module.AgentSettingsDialog
        scenario = self

        class SmokeAgentSettingsDialog(original_dialog):
            def show_modal(self):
                dialog_report = {
                    "title": self.title(),
                    "exists_before_save": bool(self.winfo_exists()),
                }
                scenario.ai_settings_dialogs[dialog_key] = dialog_report

                def invoke_save() -> None:
                    try:
                        self._on_submit()
                    except Exception as exc:
                        dialog_report["save_error"] = str(exc)
                        try:
                            self.destroy()
                        finally:
                            scenario._fail(exc)

                self.after(POLL_INTERVAL_MS, invoke_save)
                result = super().show_modal()
                dialog_report["closed"] = not _widget_exists(self)
                dialog_report["saved"] = result is not None
                return result

        main_window_execution_controls_module.AgentSettingsDialog = SmokeAgentSettingsDialog
        try:
            button.invoke()
        finally:
            main_window_execution_controls_module.AgentSettingsDialog = original_dialog

        dialog_report = self.ai_settings_dialogs.get(dialog_key, {})
        if not dialog_report.get("exists_before_save"):
            raise UiSmokeFailure(f"AI settings dialog did not open for {dialog_key}.")
        if not dialog_report.get("saved") or not dialog_report.get("closed"):
            raise UiSmokeFailure(f"AI settings dialog did not save for {dialog_key}.")
        self._pass_action(action, **dialog_report)

    def _type_prompt_text(self, session_tab_id: str) -> None:
        action = self._begin_action("type_prompt", session_tab_id=session_tab_id)
        session_widgets = self.window._get_session_widgets(session_tab_id)
        if session_widgets.prompt_text is None:
            raise UiSmokeFailure("Prompt editor is missing.")

        session_widgets.prompt_text.focus_set()
        session_widgets.prompt_text.delete("1.0", tk.END)
        session_widgets.prompt_text.insert("1.0", SMOKE_TEXT)
        self.window.update_idletasks()
        typed_text = session_widgets.prompt_text.get("1.0", tk.END).strip()
        if typed_text != SMOKE_TEXT:
            raise UiSmokeFailure("Prompt editor did not retain smoke text.")
        self._pass_action(action, character_count=len(typed_text))
        self._submit_job_via_button(session_tab_id)

    def _submit_job_via_button(self, session_tab_id: str) -> None:
        action = self._begin_action(
            "submit_job",
            session_tab_id=session_tab_id,
            trigger="session_register_button.invoke",
        )
        session_widgets = self.window._get_session_widgets(session_tab_id)
        register_button = session_widgets.register_button
        if register_button is None:
            raise UiSmokeFailure("Session register button reference is missing.")
        register_button.invoke()
        self._drain_ui_events()
        self._pass_action(action)
        self._verify_registered_jobs(session_tab_id)
        self._verify_progress_log_tab(session_tab_id)
        self._open_and_close_prompt_viewer_dialog(session_tab_id)
        self._start_queue_with_fake_agent(session_tab_id)

    def _verify_registered_jobs(self, session_tab_id: str) -> None:
        action = self._begin_action(
            "verify_job_registered",
            session_tab_id=session_tab_id,
        )
        jobs = self.window._runtime.list_jobs(session_tab_id=session_tab_id)
        prompts = tuple(job.prompt for job in jobs)
        if SMOKE_TEXT not in prompts:
            raise UiSmokeFailure("Submitted smoke prompt was not registered as a job.")

        session_tab = self.window._runtime.get_session_tab(session_tab_id)
        workspace_view = self.window._workspace_views[session_tab.workspace_tab_id]
        tree_item_count = len(workspace_view.workspace_jobs_tree.get_children())
        if tree_item_count == 0:
            raise UiSmokeFailure("Workspace job list did not show registered jobs.")
        target_jobs = [
            job for job in jobs if job.prompt in (SMOKE_TEXT, AUTO_COMMIT_PROMPT)
        ]
        workspace_tree_rows = self._assert_workspace_task_rows_match_jobs(
            session_tab.workspace_tab_id,
            target_jobs,
        )

        self._pass_action(
            action,
            runtime_job_count=len(jobs),
            tree_item_count=tree_item_count,
            workspace_tree_rows=workspace_tree_rows,
        )

        auto_commit_action = self._begin_action(
            "verify_auto_commit",
            session_tab_id=session_tab_id,
        )
        if AUTO_COMMIT_PROMPT not in prompts:
            raise UiSmokeFailure("Auto-commit follow-up job was not registered.")
        self._pass_action(auto_commit_action)

    def _verify_progress_log_tab(self, session_tab_id: str) -> None:
        action = self._begin_action(
            "verify_progress_log",
            session_tab_id=session_tab_id,
        )
        session_widgets = self.window._get_session_widgets(session_tab_id)
        selected_tab = str(session_widgets.body_notebook.select())
        progress_tab = str(session_widgets.progress_log_tab_frame)
        progress_tab_selected = selected_tab == progress_tab
        log_widget_exists = _widget_exists(session_widgets.log_text)
        if not progress_tab_selected and not log_widget_exists:
            raise UiSmokeFailure("Progress log tab was not selected and log area is missing.")
        self._pass_action(
            action,
            progress_tab_selected=progress_tab_selected,
            log_widget_exists=log_widget_exists,
        )

    def _open_and_close_prompt_viewer_dialog(self, session_tab_id: str) -> None:
        job = next(
            (
                candidate
                for candidate in self.window._runtime.list_jobs(
                    session_tab_id=session_tab_id
                )
                if candidate.prompt == SMOKE_TEXT
            ),
            None,
        )
        if job is None:
            raise UiSmokeFailure("Smoke prompt job is missing for prompt viewer.")

        action = self._begin_action(
            "open_prompt_viewer_dialog",
            job_id=job.job_id,
            trigger="workspace_job_prompt_view_command",
        )
        original_dialog = main_window_module.PromptViewerDialog
        scenario = self

        class SmokePromptViewerDialog(original_dialog):
            def show_modal(self) -> None:
                scenario.prompt_viewer_dialog = {
                    "title": self.title(),
                    "exists_before_close": bool(self.winfo_exists()),
                    "prompt": getattr(self, "_prompt", ""),
                }
                self.after(POLL_INTERVAL_MS, self.destroy)
                super().show_modal()
                scenario.prompt_viewer_dialog["closed"] = not _widget_exists(self)

        main_window_module.PromptViewerDialog = SmokePromptViewerDialog
        try:
            self.window._show_job_prompt_dialog(job.job_id)
        finally:
            main_window_module.PromptViewerDialog = original_dialog

        if not self.prompt_viewer_dialog.get("exists_before_close"):
            raise UiSmokeFailure("Prompt viewer dialog did not open.")
        if not self.prompt_viewer_dialog.get("closed"):
            raise UiSmokeFailure("Prompt viewer dialog did not close.")
        if self.prompt_viewer_dialog.get("prompt") != SMOKE_TEXT:
            raise UiSmokeFailure("Prompt viewer did not receive the smoke prompt.")
        self._pass_action(action, **self.prompt_viewer_dialog)

    def _start_queue_with_fake_agent(self, session_tab_id: str) -> None:
        session_tab = self.window._runtime.get_session_tab(session_tab_id)
        workspace_view = self.window._workspace_views[session_tab.workspace_tab_id]
        queue_button = workspace_view.queue_toggle_button
        button_state = str(queue_button.cget("state"))
        if button_state == "disabled":
            raise UiSmokeFailure("Queue start button is disabled with runnable jobs.")

        action = self._begin_action(
            "start_queue_with_fake_agent",
            session_tab_id=session_tab_id,
            workspace_tab_id=session_tab.workspace_tab_id,
            trigger="queue_toggle_button.invoke",
            button_state_before=button_state,
        )
        queue_button.invoke()
        self._drain_ui_events()
        self._pass_action(
            action,
            button_state_after=str(queue_button.cget("state")),
            queue_label=workspace_view.queue_var.get(),
        )

        self._queue_execution_session_tab_id = session_tab_id
        self._queue_completion_action = self._begin_action(
            "verify_fake_queue_completion",
            session_tab_id=session_tab_id,
            workspace_tab_id=session_tab.workspace_tab_id,
        )
        self._after(self._wait_for_fake_queue_completion)

    def _wait_for_fake_queue_completion(self) -> None:
        if self._deadline_expired("fake Codex queue completion"):
            return

        session_tab_id = self._queue_execution_session_tab_id
        action = self._queue_completion_action
        if session_tab_id is None or action is None:
            self._fail(UiSmokeFailure("Queue execution tracking is missing."))
            return

        try:
            self._drain_ui_events()
            session_tab = self.window._runtime.get_session_tab(session_tab_id)
            jobs = self.window._runtime.list_jobs(session_tab_id=session_tab_id)
            target_jobs = [
                job for job in jobs if job.prompt in (SMOKE_TEXT, AUTO_COMMIT_PROMPT)
            ]
            if len(target_jobs) < 2:
                self._after(self._wait_for_fake_queue_completion)
                return

            statuses = {job.prompt: job.status for job in target_jobs}
            terminal_statuses = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED}
            all_terminal = all(status in terminal_statuses for status in statuses.values())
            all_completed = all(status == JobStatus.COMPLETED for status in statuses.values())
            queue_state = self.window._runtime.get_queue_state(session_tab.workspace_tab_id)
            queue_stopped = queue_state.status != QueueStatus.STARTED

            if all_terminal and not all_completed:
                raise UiSmokeFailure(
                    "Fake Codex queue did not complete all target jobs: "
                    + _stringify_statuses(statuses)
                )

            if all_completed and queue_stopped:
                session_widgets = self.window._get_session_widgets(session_tab_id)
                history_text = session_widgets.history_text.get("1.0", tk.END)
                log_text = session_widgets.log_text.get("1.0", tk.END)
                history_contains_response = "UI smoke fake response for:" in history_text
                log_contains_json_event = "turn.completed" in log_text
                if not history_contains_response:
                    raise UiSmokeFailure("Session history did not render fake Codex response.")
                if not log_contains_json_event:
                    raise UiSmokeFailure("Progress log did not render fake Codex JSONL events.")

                completed_prompts = [
                    job.prompt for job in target_jobs if job.status == JobStatus.COMPLETED
                ]
                workspace_tree_rows = self._assert_workspace_task_rows_match_jobs(
                    session_tab.workspace_tab_id,
                    target_jobs,
                )
                self.queue_execution = {
                    "completed_prompts": completed_prompts,
                    "status_by_prompt": {
                        job.prompt: getattr(job.status, "value", str(job.status))
                        for job in target_jobs
                    },
                    "queue_status": getattr(queue_state.status, "value", str(queue_state.status)),
                    "queue_stop_reason": (
                        None
                        if queue_state.last_stop_reason is None
                        else getattr(
                            queue_state.last_stop_reason,
                            "value",
                            str(queue_state.last_stop_reason),
                        )
                    ),
                    "history_contains_response": history_contains_response,
                    "log_contains_json_event": log_contains_json_event,
                    "history_character_count": len(history_text.strip()),
                    "log_character_count": len(log_text.strip()),
                    "workspace_tree_completed": all(
                        row["progress"] == job_progress_text(
                            job,
                            language=getattr(self.window, "_ui_language", None),
                        )
                        for job, row in zip(target_jobs, workspace_tree_rows)
                    ),
                    "workspace_tree_rows": workspace_tree_rows,
                    "selected_log_job_id": session_widgets.rendered_log_job_id,
                    "selected_job_id": session_widgets.selected_job_id,
                }
                self._pass_action(action, **self.queue_execution)
                self._create_preset_session_via_button(session_tab.workspace_tab_id)
                return
        except Exception as exc:
            self._fail(exc)
            return

        self._after(self._wait_for_fake_queue_completion)
