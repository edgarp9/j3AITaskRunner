"""About and settings actions for MainWindow workspace controls."""

from __future__ import annotations

import logging
import sys
from tkinter import messagebox

from app.agent_cli_version import load_agent_cli_version_text
from app.version import APP_NAME, APP_VERSION
from domain.localization import normalize_ui_language

from .i18n import localize_runtime_message
from .main_window_shared import _tr_for, _window_language

LOGGER = logging.getLogger("ui.main_window")


def _main_window_global(name: str):
    return getattr(sys.modules["ui.main_window"], name)


class MainWindowWorkspaceSettingsMixin:
    def _open_about_dialog(self) -> None:
        dialog = _main_window_global("AboutDialog")(
            self,
            app_name=APP_NAME,
            app_version=APP_VERSION,
            ui_language=self._ui_language,
        )
        dialog.show_modal()

    def _open_settings_dialog(self) -> None:
        previous_settings = self._runtime.settings
        previous_language = normalize_ui_language(previous_settings.ui_language)
        previous_output_font_size = previous_settings.output_font_size
        dialog = _main_window_global("SettingsDialog")(
            self,
            self._runtime.settings,
            app_name=APP_NAME,
            app_version=APP_VERSION,
            agent_cli_version_loader=load_agent_cli_version_text,
            queue_mode_editable=getattr(
                self,
                "_queue_mode_setting_is_editable",
                lambda: True,
            )(),
        )
        result = dialog.show_modal()
        if result is None:
            return

        try:
            update_result = self._runtime.update_settings(result)
        except ValueError as error:
            messagebox.showerror(
                _tr_for(self, "dialog_settings_error"),
                localize_runtime_message(str(error), _window_language(self)),
                parent=self,
            )
            return
        except Exception:
            LOGGER.exception("Failed to update settings.")
            messagebox.showerror(
                _tr_for(self, "dialog_settings_error"),
                _tr_for(self, "dialog_settings_save_failed"),
                parent=self,
            )
            return

        next_settings = self._runtime.settings
        next_language = normalize_ui_language(next_settings.ui_language)
        output_font_size_changed = (
            next_settings.output_font_size != previous_output_font_size
        )
        self._ui_language = next_language
        if next_language != previous_language:
            self._rebuild_static_ui()
        else:
            self._refresh_settings_summary()
            if update_result.queue_mode_changed:
                self._queue_start_pending_workspace_ids.clear()
                self._refresh_all_workspace_job_views()
            if output_font_size_changed:
                self._apply_output_font_to_all_sessions()
            self._refresh_all_session_execution_option_controls()
            self._refresh_workspace_queue_summaries()
        if update_result.queue_mode_changed:
            self._set_status(
                _tr_for(
                    self,
                    "status_settings_queue_mode_changed",
                    count=update_result.cleared_job_count,
                )
            )
        else:
            self._set_status(_tr_for(self, "status_settings_saved"))

    def _refresh_all_workspace_job_views(self) -> None:
        for workspace_tab_id, workspace_view in tuple(self._workspace_views.items()):
            self._refresh_workspace_task_list(workspace_tab_id)
            for session_tab_id in tuple(workspace_view.session_views):
                if self._has_session_view(session_tab_id):
                    self._refresh_session_view(session_tab_id)

    def _queue_mode_setting_is_editable(self) -> bool:
        get_running_job = getattr(self._runtime, "get_running_job", None)
        if not callable(get_running_job):
            return True
        return get_running_job() is None
