"""Reporting and diagnostic helpers for the UI smoke scenario."""

from __future__ import annotations

from collections.abc import Sequence
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

from app.version import APP_NAME, APP_VERSION
from domain import Job
from infra.repository import PERSISTENCE_FILE_NAME
from ui.formatters import job_progress_text, truncate_prompt

from .app_process_shared import (
    POLL_INTERVAL_MS,
    UiSmokeFailure,
    _format_traceback,
    _walk_widgets,
    _widget_exists,
)


class UiSmokeReportMixin:
    def _finish_success(self) -> None:
        try:
            self._write_report(success=True)
            self.exit_code = 0
            self.finished = True
            self._restore_patches()
            self.window.close()
        except Exception as exc:
            self._fail(exc)

    def _fail(self, error: object) -> None:
        if self.finished:
            return
        self.finished = True
        self.exit_code = 1
        self._mark_last_started_action_failed(error)
        traceback_text = _format_traceback(error)
        self._write_report(
            success=False,
            error=str(error),
            traceback_text=traceback_text,
        )
        self._restore_patches()
        try:
            self.window.close()
        except tk.TclError:
            pass

    def _deadline_expired(self, description: str) -> bool:
        if time.monotonic() <= self.deadline:
            return False
        self._fail(UiSmokeFailure(f"Timed out waiting for {description}."))
        return True

    def _after(self, callback: object) -> None:
        self.window.after(POLL_INTERVAL_MS, callback)

    def _drain_ui_events(self) -> None:
        self.window._runtime.process_background_events(max_items=32)
        self.window._drain_runtime_events(max_items=32)
        self.window.update_idletasks()

    def _begin_action(self, action_id: str, **details: object) -> dict[str, object]:
        action = {"action_id": action_id, "status": "started", **details}
        self.actions.append(action)
        self.last_action = action_id
        return action

    def _pass_action(self, action: dict[str, object], **details: object) -> None:
        action.update(details)
        action["status"] = "passed"

    def _mark_action_failed(
        self,
        action: dict[str, object],
        error: object,
    ) -> None:
        action["status"] = "failed"
        action["error"] = str(error)

    def _mark_last_started_action_failed(self, error: object) -> None:
        for action in reversed(self.actions):
            if action.get("status") == "started":
                self._mark_action_failed(action, error)
                return

    def _patch_attr(self, target: object, name: str, replacement: object) -> None:
        original = getattr(target, name)
        setattr(target, name, replacement)
        self._patches.append((target, name, original))

    def _restore_patches(self) -> None:
        while self._patches:
            target, name, original = self._patches.pop()
            setattr(target, name, original)

    def _resolve_target_workspace_path(self) -> str:
        if not self.workspace_paths:
            raise UiSmokeFailure("UI smoke requires one temporary workspace path.")
        return str(Path(self.workspace_paths[0]).resolve())

    def _workspace_tab_id_for_target_path(self) -> str | None:
        expected_path = self._target_workspace_path
        if expected_path is None:
            return None
        expected_resolved = str(Path(expected_path).resolve())
        for workspace_tab_id in getattr(self.window, "_workspace_views", {}):
            workspace_tab = self.window._runtime.get_workspace_tab(workspace_tab_id)
            if str(Path(workspace_tab.workspace_path).resolve()) == expected_resolved:
                return workspace_tab_id
        return None

    def _assert_workspace_task_rows_match_jobs(
        self,
        workspace_tab_id: str,
        jobs: Sequence[Job],
    ) -> list[dict[str, object]]:
        if not jobs:
            raise UiSmokeFailure("No jobs were provided for workspace task row checks.")

        rows: list[dict[str, object]] = []
        for job in jobs:
            row = self._workspace_task_row(workspace_tab_id, job.job_id)
            expected_values = self._expected_workspace_task_row_values(job)
            actual_values = (
                row["order"],
                row["session"],
                row["progress"],
                row["prompt"],
            )
            if actual_values != expected_values:
                raise UiSmokeFailure(
                    "Workspace task row did not match job rendering. "
                    f"job_id={job.job_id} actual={actual_values!r} "
                    f"expected={expected_values!r}"
                )
            rows.append(row)
        return rows

    def _workspace_task_row(
        self,
        workspace_tab_id: str,
        job_id: str,
    ) -> dict[str, object]:
        workspace_view = self.window._workspace_views[workspace_tab_id]
        tree = workspace_view.workspace_jobs_tree
        if not tree.exists(job_id):
            raise UiSmokeFailure(f"Workspace task row is missing: {job_id}")
        values = tuple(str(value) for value in tree.item(job_id, "values"))
        if len(values) != 4:
            raise UiSmokeFailure(
                f"Workspace task row has unexpected value count: {job_id}: {values!r}"
            )
        return {
            "job_id": job_id,
            "order": values[0],
            "session": values[1],
            "progress": values[2],
            "prompt": values[3],
        }

    def _expected_workspace_task_row_values(self, job: Job) -> tuple[str, str, str, str]:
        language = getattr(self.window, "_ui_language", None)
        return (
            str(job.queue_order) if job.queue_order is not None else "-",
            self.window._job_session_label(job),
            job_progress_text(job, language=language),
            truncate_prompt(job.prompt, width=60),
        )

    def _write_report(
        self,
        *,
        success: bool,
        error: str | None = None,
        traceback_text: str | None = None,
    ) -> None:
        diagnostics = self._window_diagnostics()
        report = {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "success": success,
            "error": error,
            "user_message": error,
            "traceback": traceback_text,
            "last_action": self.last_action,
            "actions": self.actions,
            "workspace_paths": list(self.workspace_paths),
            "target_workspace_path": self._target_workspace_path,
            "storage_data_path": str(self._storage_data_path()),
            "jobs": self._job_report(),
            "saved_workspaces": self._saved_workspace_report(),
            "settings": self._settings_report(),
            "about_dialog": self.about_dialog,
            "settings_dialog": self.settings_dialog,
            "licenses_dialog": self.licenses_dialog,
            "scheduled_run_dialog": self.scheduled_run_dialog,
            "ai_settings_dialogs": self.ai_settings_dialogs,
            "prompt_viewer_dialog": self.prompt_viewer_dialog,
            "queue_execution": self.queue_execution,
            "bulk_import": self.bulk_import,
            "preset_session": self.preset_session,
            "manual_candidates": self.manual_candidates,
            "sidebar": self.sidebar,
            "window": diagnostics,
            "window_diagnostics_path": str(self._diagnostics_path()),
        }
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._diagnostics_path().write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _job_report(self) -> list[dict[str, object]]:
        try:
            jobs = self.window._runtime.list_jobs()
        except Exception:
            return []
        return [
            {
                "job_id": job.job_id,
                "workspace_tab_id": job.workspace_tab_id,
                "session_tab_id": job.session_tab_id,
                "prompt": job.prompt,
                "status": getattr(job.status, "value", str(job.status)),
            }
            for job in jobs
        ]

    def _saved_workspace_report(self) -> list[dict[str, object]]:
        try:
            saved_workspaces = self.window._runtime.list_saved_workspaces()
        except Exception:
            return []
        return [
            {
                "path": workspace.path,
                "display_name": workspace.display_name,
            }
            for workspace in saved_workspaces
        ]

    def _settings_report(self) -> dict[str, object]:
        try:
            settings = self.window._runtime.settings
        except Exception:
            return {}
        return {
            "agent_provider": settings.agent_provider,
            "executable_paths": dict(settings.executable_paths),
            "file_logging_enabled": settings.file_logging_enabled,
            "ui_language": settings.ui_language,
            "queue_mode": settings.queue_mode,
        }

    def _window_diagnostics(self) -> dict[str, object]:
        diagnostics: dict[str, object] = {
            "geometry": self._window_geometry(),
            "state": self._safe_call(lambda: self.window.state()),
            "exists": _widget_exists(self.window),
            "last_action": self.last_action,
            "status_message": self._safe_call(
                lambda: self.window._status_message_var.get()
            ),
            "workspace_count": len(getattr(self.window, "_workspace_views", {})),
            "job_count": len(self._job_report()),
        }
        workspace_snapshots: list[dict[str, object]] = []
        for workspace_tab_id, workspace_view in getattr(
            self.window,
            "_workspace_views",
            {},
        ).items():
            workspace_snapshot: dict[str, object] = {
                "workspace_tab_id": workspace_tab_id,
                "path": self._safe_call(
                    lambda view=workspace_view: view.path_var.get()
                ),
                "job_tree_items": self._safe_call(
                    lambda view=workspace_view: len(
                        view.workspace_jobs_tree.get_children()
                    )
                ),
                "job_tree_rows": self._safe_call(
                    lambda view=workspace_view: self._workspace_task_row_snapshot(
                        view.workspace_jobs_tree
                    )
                ),
                "session_count": len(workspace_view.session_views),
                "sessions": [],
            }
            sessions = workspace_snapshot["sessions"]
            if isinstance(sessions, list):
                for session_tab_id, session_widgets in workspace_view.session_views.items():
                    sessions.append(
                        {
                            "session_tab_id": session_tab_id,
                            "prompt_editor_exists": session_widgets.prompt_text is not None,
                            "selected_job_id": session_widgets.selected_job_id,
                            "activity_text": self._var_text(session_widgets.activity_var),
                            "message_text": self._var_text(session_widgets.message_var),
                            "register_button_state": self._widget_state(
                                session_widgets.register_button
                            ),
                            "progress_log_exists": _widget_exists(
                                session_widgets.log_text
                            ),
                            "log_character_count": self._text_character_count(
                                session_widgets.log_text
                            ),
                            "history_character_count": self._text_character_count(
                                session_widgets.history_text
                            ),
                            "selected_body_tab": self._safe_call(
                                lambda widgets=session_widgets: str(
                                    widgets.body_notebook.select()
                                )
                            ),
                            "candidates_status": self._var_text(
                                session_widgets.preset_candidates_status_var
                            ),
                            "candidate_count": len(
                                session_widgets.preset_candidate_ids
                            ),
                        }
                    )
            workspace_snapshots.append(workspace_snapshot)
        diagnostics["workspaces"] = workspace_snapshots
        return diagnostics

    def _storage_data_path(self) -> Path:
        repository = self.window._runtime._repository
        paths = getattr(repository, "paths", None)
        if paths is not None:
            return Path(paths.data_path)
        return Path(self.window._runtime._repository.root_dir) / PERSISTENCE_FILE_NAME

    def _diagnostics_path(self) -> Path:
        return self.report_path.with_name("window-diagnostics.json")

    def _window_geometry(self) -> str | None:
        return self._safe_call(lambda: self.window.geometry())

    def _widget_state(self, widget: object | None) -> str | None:
        if widget is None:
            return None
        return self._safe_call(lambda: str(widget.cget("state")))

    def _workspace_task_row_snapshot(
        self,
        tree: ttk.Treeview,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for item_id in tree.get_children():
            values = tuple(str(value) for value in tree.item(item_id, "values"))
            rows.append(
                {
                    "job_id": str(item_id),
                    "values": list(values),
                }
            )
        return rows

    def _text_character_count(self, widget: object | None) -> int | None:
        if widget is None:
            return None
        text = self._safe_call(lambda: widget.get("1.0", tk.END))
        if text is None:
            return None
        return len(str(text).strip())

    def _combobox_values(self, widget: object | None) -> tuple[str, ...]:
        if widget is None:
            return ()
        values = self._safe_call(lambda: widget.cget("values"))
        if values is None:
            return ()
        if isinstance(values, str):
            return tuple(value for value in values.split() if value)
        try:
            return tuple(str(value) for value in values)
        except TypeError:
            return ()

    def _var_text(self, variable: object | None) -> str:
        if variable is None:
            return ""
        value = self._safe_call(lambda: variable.get())
        return str(value).strip() if value is not None else ""

    def _find_button_by_text(self, root: tk.Misc, text: str) -> tk.Misc | None:
        for widget in _walk_widgets(root):
            try:
                if isinstance(widget, ttk.Button) and str(widget.cget("text")) == text:
                    return widget
            except tk.TclError:
                continue
        return None

    def _safe_call(callback):
        try:
            return callback()
        except Exception:
            return None
