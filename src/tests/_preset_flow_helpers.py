from __future__ import annotations

import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from app.use_cases import (
    prepare_manual_preset_work_generation_prompt,
    parse_preset_generated_work_prompts,
    prepare_preset_work_generation_prompt,
)
from app.controller import (
    AppController,
    JobExecutionResultCapturedEvent,
    JobStatusChangedEvent,
)
from app.runtime import (
    AUTO_COMMIT_PROMPT,
    AppRuntime,
    PRESET_WORK_PRIORITY_OPTIONS,
    PresetManualCandidateSelectionClearedEvent,
    PresetManualCandidateSelectionContinuedEvent,
    PresetManualCandidateSelectionRequiredEvent,
    PresetCandidateJobsRegisteredEvent,
    RuntimeActionFailedEvent,
    _PresetAnalysisJobContext,
    _PresetManualSelectionContext,
    _PresetWorkGenerationJobContext,
    _RuntimeActionCompletion,
    _build_preset_analysis_prompt,
)
from domain import (
    AgentExecutionOptions,
    AppSettings,
    JobStatus,
    PresetAnalysisError,
    PresetCandidate,
    QueueStopReason,
    SessionTabKind,
    TabOpenState,
    WorkspaceQueueState,
    build_candidates_payload,
    extract_candidates,
    extract_generated_work_prompts,
    parse_json_object_from_text,
    render_work_prompt_template,
    select_manual_work_candidates,
    select_work_candidates,
)
from infra.repository import PromptStore
from infra.process_runner import AgentRunResult, AgentRunStatus, ExecutionArtifactPaths


def _candidate_payload(
    candidate_id: str,
    *,
    priority: str = "medium",
    evidence: str | list[str] = "app/example.py:10",
) -> dict[str, object]:
    return {
        "id": candidate_id,
        "title": f"title {candidate_id}",
        "problem": f"problem {candidate_id}",
        "evidence": evidence,
        "priority": priority,
        "risk": "medium",
        "impact": f"impact {candidate_id}",
    }


def _candidate(candidate_id: str, *, priority: str = "medium") -> PresetCandidate:
    return PresetCandidate(
        id=candidate_id,
        title=f"title {candidate_id}",
        problem=f"problem {candidate_id}",
        evidence="app/example.py:10",
        priority=priority,
        risk="medium",
        impact=f"impact {candidate_id}",
    )


def _analysis_text(candidates: list[dict[str, object]]) -> str:
    return json.dumps({"candidates": candidates}, ensure_ascii=False)

def _candidate_payload(
    candidate_id: str,
    *,
    priority: str = "medium",
    evidence: str | list[str] = "app/example.py:10",
) -> dict[str, object]:
    return {
        "id": candidate_id,
        "title": f"title {candidate_id}",
        "problem": f"problem {candidate_id}",
        "evidence": evidence,
        "priority": priority,
        "risk": "medium",
        "impact": f"impact {candidate_id}",
    }

def _candidate(candidate_id: str, *, priority: str = "medium") -> PresetCandidate:
    return PresetCandidate(
        id=candidate_id,
        title=f"title {candidate_id}",
        problem=f"problem {candidate_id}",
        evidence="app/example.py:10",
        priority=priority,
        risk="medium",
        impact=f"impact {candidate_id}",
    )

def _analysis_text(candidates: list[dict[str, object]]) -> str:
    return json.dumps({"candidates": candidates}, ensure_ascii=False)

def _build_runtime_for_preset_flow() -> AppRuntime:
    runtime = AppRuntime.__new__(AppRuntime)
    runtime._controller = _PresetRuntimeControllerStub()
    runtime._event_queue = _RuntimeEventQueueStub()
    runtime._prompt_store = _PresetPromptStoreStub()
    runtime._controller_state_lock = threading.RLock()
    runtime._queue_control_global_generation = 0
    runtime._queue_control_workspace_generations = {}
    runtime._queue_control_lock = threading.Lock()
    runtime._preset_followup_lock = threading.Lock()
    runtime._preset_followup_pending_workspace_counts = {}
    runtime._runtime_action_shutdown_requested = False
    runtime._preset_analysis_job_contexts = {}
    runtime._preset_work_generation_job_contexts = {}
    runtime._preset_manual_selection_contexts = {}
    runtime._dispatch_action_lock = threading.Lock()
    runtime._dispatch_action_requested = False
    runtime._job_user_messages = {}
    runtime._job_progress_logs = {}
    return runtime

class _PresetPromptStoreStub:
    def read_analysis_prompt(self, language: str, instruction: str) -> str:
        self.language = language
        self.instruction = instruction
        return "analysis prompt"

    def read_work_prompt_template(self, language: str, instruction: str) -> str:
        self.language = language
        self.instruction = instruction
        return "work {{candidates_payload}}"

class _PresetRuntimeControllerStub:
    def __init__(self) -> None:
        self.session_manager = _PresetRuntimeSessionManagerStub()
        self.submitted_jobs: list[tuple[str, str]] = []
        self.submitted_execution_options: list[AgentExecutionOptions | None] = []
        self.submitted_force_fresh_sessions: list[bool] = []
        self.started_queue_ids: list[str | None] = []
        self.stopped_queues: list[tuple[str | None, QueueStopReason | str]] = []
        self.prioritized_job_ids: tuple[str, ...] = ()
        self.pending_dispatch = False
        self.pending_dispatch_workspace_tab_ids_value: tuple[str, ...] = ()
        self.dispatch_next_job_calls = 0
        self.dispatch_excluded_workspace_tab_ids: list[tuple[str, ...]] = []
        self.running_status_events: list[JobStatusChangedEvent] = []
        self._ui_events: list[object] = []

    @property
    def opened_parent_ids(self) -> list[str]:
        return self.session_manager.opened_parent_ids

    def submit_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        dispatch_immediately: bool = True,
        force_fresh_session: bool = False,
        execution_options: AgentExecutionOptions | None = None,
    ):
        del dispatch_immediately
        self.submitted_jobs.append((session_tab_id, prompt))
        self.submitted_execution_options.append(execution_options)
        self.submitted_force_fresh_sessions.append(force_fresh_session)
        return _RuntimeJobStub(f"job-{len(self.submitted_jobs)}")

    def submit_jobs(
        self,
        job_requests: list[tuple[str, str]],
        *,
        dispatch_immediately: bool = True,
        execution_options: AgentExecutionOptions | None = None,
    ) -> tuple["_RuntimeJobStub", ...]:
        del dispatch_immediately
        jobs: list[_RuntimeJobStub] = []
        for session_tab_id, prompt in job_requests:
            self.submitted_jobs.append((session_tab_id, prompt))
            self.submitted_execution_options.append(execution_options)
            jobs.append(_RuntimeJobStub(f"job-{len(self.submitted_jobs)}"))
        return tuple(jobs)

    def start_queue(self, workspace_tab_id: str | None = None) -> WorkspaceQueueState:
        self.started_queue_ids.append(workspace_tab_id)
        return WorkspaceQueueState(workspace_tab_id=workspace_tab_id or "workspace-1")

    def stop_queue(
        self,
        workspace_tab_id: str | None = None,
        *,
        reason: QueueStopReason | str = QueueStopReason.USER_STOPPED,
    ) -> WorkspaceQueueState:
        self.stopped_queues.append((workspace_tab_id, reason))
        return WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id or "workspace-1",
            last_stop_reason=reason,
        )

    def prioritize_queued_jobs(self, job_ids: list[str]) -> tuple[object, ...]:
        self.prioritized_job_ids = tuple(job_ids)
        return ()

    def has_pending_dispatch(self) -> bool:
        return self.pending_dispatch

    def pending_dispatch_workspace_tab_ids(self) -> tuple[str, ...]:
        return self.pending_dispatch_workspace_tab_ids_value

    def dispatch_next_job(self, *, excluded_workspace_tab_ids=()) -> None:
        self.dispatch_excluded_workspace_tab_ids.append(
            tuple(sorted(excluded_workspace_tab_ids))
        )
        self.dispatch_next_job_calls += 1
        self.pending_dispatch = False
        self.pending_dispatch_workspace_tab_ids_value = ()
        self._ui_events.extend(self.running_status_events)
        self.running_status_events.clear()

    def drain_ui_events(self) -> tuple[object, ...]:
        events = tuple(self._ui_events)
        self._ui_events.clear()
        return events

class _PresetRuntimeSessionManagerStub:
    def __init__(self) -> None:
        self.opened_parent_ids: list[str] = []
        self.candidate_session_execution_options: list[
            AgentExecutionOptions | None
        ] = []
        self.sessions: dict[str, _RuntimeSessionStub] = {
            "preset-parent": _RuntimeSessionStub("preset-parent"),
        }

    def get_session_tab(self, session_tab_id: str) -> "_RuntimeSessionStub":
        session_tab = self.sessions.get(session_tab_id)
        if session_tab is None:
            session_tab = _RuntimeSessionStub(session_tab_id)
            self.sessions[session_tab_id] = session_tab
        return session_tab

    def open_preset_candidate_session(self, parent_session_tab_id: str):
        parent_session = self.get_session_tab(parent_session_tab_id)
        if parent_session.open_state != TabOpenState.OPEN:
            raise ValueError("Cannot open a preset candidate for a closed parent tab.")
        self.opened_parent_ids.append(parent_session_tab_id)
        session_tab = _RuntimeSessionStub(f"candidate-{len(self.opened_parent_ids)}")
        self.sessions[session_tab.session_tab_id] = session_tab
        return session_tab

    def open_preset_candidate_sessions(
        self,
        parent_session_tab_id: str,
        *,
        count: int,
        execution_options: AgentExecutionOptions | None = None,
    ) -> tuple["_RuntimeSessionStub", ...]:
        candidate_sessions: list[_RuntimeSessionStub] = []
        for _ in range(count):
            candidate_session = self.open_preset_candidate_session(parent_session_tab_id)
            candidate_session.execution_options = execution_options
            candidate_sessions.append(candidate_session)
            self.candidate_session_execution_options.append(execution_options)
        return tuple(candidate_sessions)

class _RuntimeEventQueueStub:
    def __init__(self) -> None:
        self.events: list[object] = []

    def put(self, event: object) -> None:
        self.events.append(event)

class _RuntimeActionRequestQueueStub:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def put(self, request: object) -> None:
        self.requests.append(request)

class _MarkPresetFollowupPendingOnEnterLock:
    def __init__(self, callback) -> None:
        self._lock = threading.RLock()
        self._callback = callback
        self._callback_called = False

    def __enter__(self):
        self._lock.__enter__()
        if not self._callback_called:
            self._callback_called = True
            self._callback()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._lock.__exit__(exc_type, exc_value, traceback)

class _RuntimeJobStub:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

class _ActiveWorkspaceManagerStub:
    def __init__(self, workspace_tab_id: str) -> None:
        self._workspace_tab = _RuntimeWorkspaceTabStub(workspace_tab_id)

    def get_active_workspace_tab(self) -> "_RuntimeWorkspaceTabStub":
        return self._workspace_tab

class _RuntimeWorkspaceTabStub:
    def __init__(self, workspace_tab_id: str) -> None:
        self.workspace_tab_id = workspace_tab_id

class _RuntimeSessionStub:
    def __init__(
        self,
        session_tab_id: str,
        *,
        open_state: TabOpenState = TabOpenState.OPEN,
        execution_options: AgentExecutionOptions | None = None,
    ) -> None:
        self.session_tab_id = session_tab_id
        self.open_state = open_state
        self.execution_options = execution_options

class _RuntimeRepositoryStub:
    def load_settings(self) -> AppSettings:
        return AppSettings()

    def save_settings(self, settings: AppSettings) -> None:
        del settings

    def load_saved_workspaces(self) -> tuple[object, ...]:
        return ()

    def save_saved_workspaces(self, workspaces: tuple[object, ...]) -> None:
        del workspaces

class _ImmediatePresetRunner:
    def __init__(self, artifacts_root: Path) -> None:
        self._artifacts_root = artifacts_root
        self.launched_prompts: list[str] = []
        self.launched_settings: list[AppSettings] = []
        self.launched_execution_options: list[AgentExecutionOptions] = []

    def validate(self, request) -> str | None:
        if not Path(request.operational_settings.executable_path or "").is_file():
            return "실행기 경로를 확인하세요."
        if not Path(request.workspace_path).is_dir():
            return "워크스페이스 경로를 확인하세요."
        return None

    def launch(
        self,
        request,
        *,
        on_stdout_line=None,
        on_stderr_line=None,
        on_json_event=None,
        on_handle_created=None,
    ) -> "_ImmediatePresetHandle":
        del on_stdout_line, on_stderr_line, on_json_event
        self.launched_prompts.append(request.prompt)
        self.launched_settings.append(request.operational_settings)
        self.launched_execution_options.append(request.execution_options)
        handle = _ImmediatePresetHandle(
            request.job_id,
            self._build_result(request),
        )
        if on_handle_created is not None:
            on_handle_created(handle)
        return handle

    def cancel(self, handle: "_ImmediatePresetHandle") -> None:
        del handle

    def _build_result(self, request) -> AgentRunResult:
        artifacts = _create_execution_artifacts(self._artifacts_root, request.job_id)
        if "analysis prompt" in request.prompt:
            last_message = _analysis_text(
                [
                    _candidate_payload("1", priority="high"),
                    _candidate_payload("2", priority="medium"),
                    _candidate_payload("3", priority="low"),
                ]
            )
        elif request.prompt.startswith("work "):
            last_message = json.dumps(
                {
                    "prompts": [
                        {
                            "candidate_id": "2",
                            "title": "candidate two",
                            "prompt": "/goal candidate two",
                        },
                        {
                            "candidate_id": "1",
                            "title": "candidate one",
                            "prompt": "/goal candidate one",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        else:
            last_message = "done"
        artifacts.last_message_path.write_text(last_message, encoding="utf-8")
        return AgentRunResult(
            status=AgentRunStatus.COMPLETED,
            command=("fake-agent", request.job_id),
            artifacts=artifacts,
            exit_code=0,
            session_id=request.session_id or f"thread-{request.job_id}",
            last_message=last_message,
        )

class _DeferredFirstPresetRunner(_ImmediatePresetRunner):
    def __init__(self, artifacts_root: Path) -> None:
        super().__init__(artifacts_root)
        self._deferred_handles: dict[str, _DeferredPresetHandle] = {}

    def launch(
        self,
        request,
        *,
        on_stdout_line=None,
        on_stderr_line=None,
        on_json_event=None,
        on_handle_created=None,
    ):
        del on_stdout_line, on_stderr_line, on_json_event
        result = self._build_result(request)
        if "선택된 Work Priority:" in request.prompt:
            handle = _DeferredPresetHandle(request.job_id, result)
            self._deferred_handles[request.job_id] = handle
        else:
            handle = _ImmediatePresetHandle(request.job_id, result)
        self.launched_prompts.append(request.prompt)
        if on_handle_created is not None:
            on_handle_created(handle)
        return handle

    def resolve(self, job_id: str) -> None:
        self._deferred_handles[job_id].resolve()

    def cancel(self, handle) -> None:
        if isinstance(handle, _DeferredPresetHandle):
            handle.resolve()

class _PromptAssetPresetRunner:
    def __init__(self, artifacts_root: Path) -> None:
        self._artifacts_root = artifacts_root
        self.launched_prompts: list[str] = []

    def validate(self, request) -> str | None:
        if not Path(request.operational_settings.executable_path or "").is_file():
            return "실행기 경로를 확인하세요."
        if not Path(request.workspace_path).is_dir():
            return "워크스페이스 경로를 확인하세요."
        return None

    def launch(
        self,
        request,
        *,
        on_stdout_line=None,
        on_stderr_line=None,
        on_json_event=None,
        on_handle_created=None,
    ) -> "_ImmediatePresetHandle":
        del on_stdout_line, on_stderr_line, on_json_event
        self.launched_prompts.append(request.prompt)
        handle = _ImmediatePresetHandle(
            request.job_id,
            self._build_result(request),
        )
        if on_handle_created is not None:
            on_handle_created(handle)
        return handle

    def cancel(self, handle: "_ImmediatePresetHandle") -> None:
        del handle

    def _build_result(self, request) -> AgentRunResult:
        artifacts = _create_execution_artifacts(self._artifacts_root, request.job_id)
        if "/goal 당신은 Python 및 Tkinter" in request.prompt:
            last_message = _analysis_text(
                [
                    _candidate_payload("1", priority="high"),
                    _candidate_payload("2", priority="medium"),
                    _candidate_payload("3", priority="low"),
                ]
            )
        elif "입력 후보 JSON:" in request.prompt:
            last_message = json.dumps(
                {
                    "prompts": [
                        {
                            "candidate_id": "2",
                            "title": "candidate two",
                            "prompt": "/goal prompt asset candidate two",
                        },
                        {
                            "candidate_id": "1",
                            "title": "candidate one",
                            "prompt": "/goal prompt asset candidate one",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        else:
            last_message = "done"
        artifacts.last_message_path.write_text(last_message, encoding="utf-8")
        return AgentRunResult(
            status=AgentRunStatus.COMPLETED,
            command=("fake-agent", request.job_id),
            artifacts=artifacts,
            exit_code=0,
            session_id=request.session_id or f"thread-{request.job_id}",
            last_message=last_message,
        )

class _ImmediatePresetHandle:
    command = ("fake-agent",)

    def __init__(self, handle_id: str, result: AgentRunResult) -> None:
        self.handle_id = handle_id
        self._result = result
        self.artifacts = result.artifacts

    def wait(self, timeout: float | None = None) -> AgentRunResult:
        del timeout
        return self._result

class _DeferredPresetHandle(_ImmediatePresetHandle):
    def __init__(self, handle_id: str, result: AgentRunResult) -> None:
        super().__init__(handle_id, result)
        self._resolved = threading.Event()

    def resolve(self) -> None:
        self._resolved.set()

    def wait(self, timeout: float | None = None) -> AgentRunResult:
        if not self._resolved.wait(timeout):
            raise TimeoutError(f"Deferred preset handle was not resolved: {self.handle_id}")
        return self._result

def _create_execution_artifacts(root: Path, job_id: str) -> ExecutionArtifactPaths:
    artifact_dir = root / job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = artifact_dir / "prompt.txt"
    stdout_jsonl_path = artifact_dir / "stdout.jsonl"
    stderr_log_path = artifact_dir / "stderr.log"
    last_message_path = artifact_dir / "last_message.txt"
    launch_metadata_path = artifact_dir / "launch.json"
    for path in (prompt_path, stdout_jsonl_path, stderr_log_path, launch_metadata_path):
        path.touch()
    return ExecutionArtifactPaths(
        root_dir=artifact_dir,
        prompt_path=prompt_path,
        stdout_jsonl_path=stdout_jsonl_path,
        stderr_log_path=stderr_log_path,
        last_message_path=last_message_path,
        launch_metadata_path=launch_metadata_path,
    )

def _drain_until(
    runtime: AppRuntime,
    predicate,
    *,
    timeout: float = 3.0,
    interval: float = 0.01,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime.process_background_events()
        runtime.drain_events()
        if predicate():
            return True
        time.sleep(interval)
    runtime.process_background_events()
    runtime.drain_events()
    return predicate()

__all__ = [name for name in globals() if not name.startswith("__")]
