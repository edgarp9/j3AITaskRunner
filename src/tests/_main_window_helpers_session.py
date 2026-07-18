from __future__ import annotations

from tests._main_window_helpers_core import *

from tests._main_window_helpers_session_tabs import *

@dataclass(slots=True)
class _PresetCandidateSessionWidgetsStub:
    auto_commit_var: _BoolVarStub

@dataclass(slots=True)
class _OrderedSessionTabStub:
    session_tab_id: str

@dataclass(slots=True)
class _SessionOrderWorkspaceViewStub:
    session_views: dict[str, object]
    session_notebook: "_SessionOrderNotebookStub"

class _SessionOrderNotebookStub:
    def __init__(self, tab_count: int) -> None:
        self._tab_count = tab_count

    def tabs(self) -> tuple[str, ...]:
        return tuple(f"tab-{index}" for index in range(self._tab_count))

class _SessionOrderRuntimeStub:
    def __init__(self, ordered_session_ids: tuple[str, ...]) -> None:
        self._ordered_session_ids = ordered_session_ids

    def list_session_tabs(
        self,
        workspace_tab_id: str,
        *,
        include_closed: bool = False,
    ) -> tuple[_OrderedSessionTabStub, ...]:
        del workspace_tab_id, include_closed
        return tuple(
            _OrderedSessionTabStub(session_tab_id)
            for session_tab_id in self._ordered_session_ids
        )

class _SessionOrderWindowStub:
    def __init__(
        self,
        *,
        ordered_session_ids: tuple[str, ...],
        existing_session_ids: tuple[str, ...],
    ) -> None:
        self._runtime = _SessionOrderRuntimeStub(ordered_session_ids)
        self._workspace_views = {
            "workspace-1": _SessionOrderWorkspaceViewStub(
                session_views={
                    session_tab_id: object() for session_tab_id in existing_session_ids
                },
                session_notebook=_SessionOrderNotebookStub(len(existing_session_ids)),
            )
        }

class _SessionCloseSelectionRuntimeStub:
    def get_session_tab(self, session_tab_id: str) -> "_ClosedSessionTabStub":
        del session_tab_id
        return _ClosedSessionTabStub(workspace_tab_id="workspace-1")

class _SessionCloseSelectionFrameStub:
    def __init__(self, tab_id: str) -> None:
        self._tab_id = tab_id
        self.destroy_calls = 0

    def __str__(self) -> str:
        return self._tab_id

    def destroy(self) -> None:
        self.destroy_calls += 1

@dataclass(slots=True)
class _SessionCloseSelectionWidgetsStub:
    frame: _SessionCloseSelectionFrameStub

class _SessionCloseSelectionNotebookStub:
    def __init__(self, tab_ids: tuple[str, ...]) -> None:
        self._tab_ids = list(tab_ids)
        self.forgotten_tab_ids: list[str] = []
        self.selected_tab_ids: list[str] = []

    def tabs(self) -> tuple[str, ...]:
        return tuple(self._tab_ids)

    def forget(self, frame: _SessionCloseSelectionFrameStub) -> None:
        tab_id = str(frame)
        self.forgotten_tab_ids.append(tab_id)
        if tab_id in self._tab_ids:
            self._tab_ids.remove(tab_id)

    def select(self, tab_id: str) -> None:
        self.selected_tab_ids.append(str(tab_id))

@dataclass(slots=True)
class _SessionCloseSelectionWorkspaceViewStub:
    session_views: dict[str, _SessionCloseSelectionWidgetsStub]
    session_notebook: _SessionCloseSelectionNotebookStub

class _SessionCloseSelectionWindowStub:
    _session_tab_to_select_after_close = MainWindow._session_tab_to_select_after_close

    def __init__(self, ordered_session_ids: tuple[str, ...]) -> None:
        self._runtime = _SessionCloseSelectionRuntimeStub()
        self._preset_language_request_ids: dict[str, int] = {}
        self._preset_instruction_request_ids: dict[str, int] = {}
        self._preset_registration_pending_session_ids: set[str] = set()
        self._immediate_run_pending_session_ids: set[str] = set()
        self._frames_by_session_id = {
            session_tab_id: _SessionCloseSelectionFrameStub(f"frame-{session_tab_id}")
            for session_tab_id in ordered_session_ids
        }
        session_views = {
            session_tab_id: _SessionCloseSelectionWidgetsStub(frame=frame)
            for session_tab_id, frame in self._frames_by_session_id.items()
        }
        self._session_frame_map = {
            str(frame): ("workspace-1", session_tab_id)
            for session_tab_id, frame in self._frames_by_session_id.items()
        }
        self.session_notebook = _SessionCloseSelectionNotebookStub(
            tuple(
                str(self._frames_by_session_id[session_tab_id])
                for session_tab_id in ordered_session_ids
            )
        )
        self._workspace_views = {
            "workspace-1": _SessionCloseSelectionWorkspaceViewStub(
                session_views=session_views,
                session_notebook=self.session_notebook,
            )
        }

    def frame_for_session(self, session_tab_id: str) -> _SessionCloseSelectionFrameStub:
        return self._frames_by_session_id[session_tab_id]

class _PresetCandidateRegistrationWindowStub(_KoreanUiLanguageStub):
    def __init__(self) -> None:
        self.session_widgets: dict[str, _PresetCandidateSessionWidgetsStub] = {}
        self.ensured_session_ids: list[str] = []
        self.refreshed_session_ids: list[str] = []
        self.refreshed_workspace_ids: list[str] = []
        self.synced_workspace_ids: list[str] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []

    def _ensure_session_view(self, session_tab_id: str) -> _PresetCandidateSessionWidgetsStub:
        self.ensured_session_ids.append(session_tab_id)
        self.session_widgets.setdefault(
            session_tab_id,
            _PresetCandidateSessionWidgetsStub(auto_commit_var=_BoolVarStub(False)),
        )
        return self.session_widgets[session_tab_id]

    def _has_session_view(self, session_tab_id: str) -> bool:
        return session_tab_id in self.session_widgets

    def _refresh_session_view(self, session_tab_id: str) -> None:
        self.refreshed_session_ids.append(session_tab_id)

    def _refresh_workspace_task_list(self, workspace_tab_id: str) -> None:
        self.refreshed_workspace_ids.append(workspace_tab_id)

    def _sync_session_tab_order(self, workspace_tab_id: str) -> None:
        self.synced_workspace_ids.append(workspace_tab_id)

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

class _TextWidgetStub:
    def __init__(self, *, content: str, yview: tuple[float, float]) -> None:
        self.content = content
        self._yview = yview
        self.states: list[str] = []
        self.see_calls: list[str] = []

    def configure(self, *, state: str) -> None:
        self.states.append(state)

    def delete(self, start: str, end: str) -> None:
        del start, end
        self.content = ""

    def insert(self, index: str, content: str) -> None:
        del index
        self.content += content

    def get(self, start: str, end: str) -> str:
        del start, end
        return self.content

    def yview(self) -> tuple[float, float]:
        return self._yview

    def see(self, index: str) -> None:
        self.see_calls.append(index)

@dataclass(slots=True)
class _SessionSelectionWidgetsStub:
    selected_job_id: str | None
    log_text: _TextWidgetStub
    body_notebook: _BodyNotebookSelectStub = field(
        default_factory=_BodyNotebookSelectStub
    )
    progress_log_tab_frame: object = field(default_factory=object)
    rendered_log_job_id: str | None = None
    rendered_log_line_count: int = 0
    rendered_log_last_line: str | None = None
    rendered_log_language: str | None = None
    session_id_var: _StringVarStub = field(default_factory=_StringVarStub)
    activity_var: _StringVarStub = field(default_factory=_StringVarStub)
    message_var: _StringVarStub = field(default_factory=_StringVarStub)
    wait_reason_var: _StringVarStub = field(default_factory=_StringVarStub)
    message_label: _LabelVisibilityStub = field(default_factory=_LabelVisibilityStub)
    wait_reason_label: _LabelVisibilityStub = field(
        default_factory=_LabelVisibilityStub
    )
    immediate_run_button: _ButtonConfigureStub = field(
        default_factory=_ButtonConfigureStub
    )

class _SessionSelectionRuntimeStub:
    def __init__(
        self,
        jobs: tuple[Job, ...],
        *,
        progress_logs: dict[str, tuple[str, ...]] | None = None,
        job_user_messages: dict[str, str] | None = None,
        session_kind: SessionTabKind = SessionTabKind.NORMAL,
    ) -> None:
        self._jobs = jobs
        self._progress_logs = progress_logs or {}
        self._job_user_messages = job_user_messages or {}
        self._session_kind = session_kind

    def list_jobs(self, *, session_tab_id: str | None = None) -> tuple[Job, ...]:
        del session_tab_id
        return self._jobs

    def get_session_tab(self, session_tab_id: str) -> SessionTab:
        return SessionTab(
            session_tab_id=session_tab_id,
            workspace_tab_id="workspace-1",
            display_name="S1",
            kind=self._session_kind,
            session_id="session-id-1",
        )

    def list_session_turns(self, session_tab_id: str) -> tuple[object, ...]:
        del session_tab_id
        return ()

    def get_job(self, job_id: str) -> Job:
        for job in self._jobs:
            if job.job_id == job_id:
                return job
        raise KeyError(job_id)

    def get_job_user_message(self, job_id: str) -> str:
        return self._job_user_messages.get(job_id, "")

    def get_job_progress_logs(self, job_id: str) -> tuple[str, ...]:
        return self._progress_logs.get(job_id, ())

class _SessionSelectionWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        jobs: tuple[Job, ...],
        *,
        selected_job_id: str | None,
        progress_logs: dict[str, tuple[str, ...]] | None = None,
        job_user_messages: dict[str, str] | None = None,
        session_kind: SessionTabKind = SessionTabKind.NORMAL,
    ) -> None:
        self._runtime = _SessionSelectionRuntimeStub(
            jobs,
            progress_logs=progress_logs,
            job_user_messages=job_user_messages,
            session_kind=session_kind,
        )
        self.session_widgets = _SessionSelectionWidgetsStub(
            selected_job_id,
            _TextWidgetStub(content="", yview=(0.0, 1.0)),
        )
        self.session_tab_indicator_calls: list[tuple[str, bool]] = []
        self._immediate_run_pending_session_ids: set[str] = set()

    def _get_session_widgets(self, session_tab_id: str) -> _SessionSelectionWidgetsStub:
        del session_tab_id
        return self.session_widgets

    def _refresh_session_tab_indicator(
        self, session_tab_id: str, *, started: bool
    ) -> None:
        self.session_tab_indicator_calls.append((session_tab_id, started))

    def _set_text_content(
        self,
        widget: _TextWidgetStub,
        content: str,
        *,
        auto_scroll_to_end: bool = False,
    ) -> None:
        MainWindow._set_text_content(
            self,
            widget,
            content,
            auto_scroll_to_end=auto_scroll_to_end,
        )

    def _append_text_content(
        self,
        widget: _TextWidgetStub,
        content: str,
        *,
        prefix_separator: bool,
        auto_scroll_to_end: bool = False,
    ) -> None:
        MainWindow._append_text_content(
            self,
            widget,
            content,
            prefix_separator=prefix_separator,
            auto_scroll_to_end=auto_scroll_to_end,
        )

    def _append_session_output_lines(
        self,
        session_widgets: _SessionSelectionWidgetsStub,
        lines: list[str],
        *,
        language: str,
    ) -> None:
        MainWindow._append_session_output_lines(
            self,
            session_widgets,
            lines,
            language=language,
        )

    def _trim_rendered_session_output_lines(
        self,
        session_widgets: _SessionSelectionWidgetsStub,
    ) -> None:
        MainWindow._trim_rendered_session_output_lines(self, session_widgets)

    def _select_appended_running_job(
        self,
        session_widgets: _SessionSelectionWidgetsStub,
        *,
        selected_job_id: str,
        appended_job_id: str,
    ) -> str:
        return MainWindow._select_appended_running_job(
            self,
            session_widgets,
            selected_job_id=selected_job_id,
            appended_job_id=appended_job_id,
        )

    def _mark_session_output_rendered(
        self,
        session_widgets: _SessionSelectionWidgetsStub,
        *,
        job_id: str | None,
        line_count: int,
        last_line: str | None,
        language: str | None,
    ) -> None:
        MainWindow._mark_session_output_rendered(
            self,
            session_widgets,
            job_id=job_id,
            line_count=line_count,
            last_line=last_line,
            language=language,
        )

@dataclass(slots=True)
class _WorkspaceJobSelectionWorkspaceViewStub:
    session_views: dict[str, _SessionSelectionWidgetsStub]

class _WorkspaceJobSelectionRuntimeStub:
    def __init__(self, job: Job) -> None:
        self._job = job

    def get_job(self, job_id: str) -> Job:
        if job_id != self._job.job_id:
            raise KeyError(job_id)
        return self._job

class _WorkspaceJobSelectionWindowStub(_KoreanUiLanguageStub):
    def __init__(self, job: Job) -> None:
        self._runtime = _WorkspaceJobSelectionRuntimeStub(job)
        self.session_widgets = _SessionSelectionWidgetsStub(
            selected_job_id=None,
            log_text=_TextWidgetStub(content="", yview=(0.0, 1.0)),
        )
        self._workspace_views = {
            job.workspace_tab_id: _WorkspaceJobSelectionWorkspaceViewStub(
                session_views={job.session_tab_id: self.session_widgets}
            )
        }
        self.selected_session_ids: list[tuple[str, str]] = []
        self.refreshed_summary_ids: list[str] = []
        self.refreshed_output_ids: list[str] = []

    def _get_session_widgets(self, session_tab_id: str) -> _SessionSelectionWidgetsStub:
        del session_tab_id
        return self.session_widgets

    def _select_session_tab(self, workspace_tab_id: str, session_tab_id: str) -> None:
        self.selected_session_ids.append((workspace_tab_id, session_tab_id))

    def _refresh_session_summary(self, session_tab_id: str) -> None:
        self.refreshed_summary_ids.append(session_tab_id)

    def _refresh_session_output(self, session_tab_id: str) -> None:
        self.refreshed_output_ids.append(session_tab_id)



















__all__ = [name for name in globals() if not name.startswith("__")]
