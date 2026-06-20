from __future__ import annotations

from collections.abc import ValuesView
from dataclasses import dataclass, replace
import threading
import time
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from app.controller import LogAppendedEvent
from app.runtime import (
    AppRuntime,
    AUTO_COMMIT_PROMPT,
    PersistenceIssueEvent,
    QueueStartCompletedEvent,
    SettingsRetryCompletedEvent,
    WorkspaceOpenCompletedEvent,
    _RuntimeActionCompletion,
)
from app.scheduler import (
    ExecutionHandle,
    JobExecutionRequest,
    Scheduler,
    WorkspaceJobSummary,
)
from app.session_manager import SessionManager
from app.workspace_manager import WorkspaceManager
from domain.models import (
    AgentExecutionOptions,
    AppSettings,
    Job,
    JobStatus,
    QueueStatus,
    QueueStopReason,
    SavedWorkspace,
    SessionTab,
    SessionTabKind,
    TabOpenState,
    WorkspaceQueueState,
)
from infra.repository import PersistenceSaveError


def _dt(minutes: int) -> datetime:
    base = datetime(2026, 4, 22, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes)


class WorkspaceManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = WorkspaceManager()

    def test_open_workspace_reuses_existing_open_tab_for_same_path(self) -> None:
        first = self.manager.open_validated_workspace(r"C:\Repo\Alpha", when=_dt(0))
        duplicated = self.manager.open_validated_workspace(r"c:/repo/alpha\\", when=_dt(1))

        self.assertTrue(first.created)
        self.assertFalse(duplicated.created)
        self.assertEqual(
            first.workspace_tab.workspace_tab_id,
            duplicated.workspace_tab.workspace_tab_id,
        )
        self.assertEqual(1, len(self.manager.list_workspace_tabs(include_closed=False)))
        self.assertEqual(
            first.workspace_tab.workspace_tab_id,
            self.manager.active_workspace_tab_id,
        )

    def test_workspace_display_name_uses_final_folder_name(self) -> None:
        first = self.manager.open_validated_workspace(r"C:\Repo\One", when=_dt(0)).workspace_tab
        second = self.manager.open_validated_workspace(r"C:\Repo\Two", when=_dt(1)).workspace_tab

        self.assertEqual("One", first.display_name)
        self.assertEqual("Two", second.display_name)

        self.manager.close_workspace(first.workspace_tab_id, when=_dt(2))
        self.manager.close_workspace(second.workspace_tab_id, when=_dt(3))

        reopened = self.manager.open_validated_workspace(r"C:\Repo\Three", when=_dt(4)).workspace_tab

        self.assertEqual("Three", reopened.display_name)
        self.assertEqual(TabOpenState.OPEN, reopened.open_state)


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_manager = WorkspaceManager()
        self.session_manager = SessionManager(self.workspace_manager)
        self.workspace_tab = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Alpha",
            when=_dt(0),
        ).workspace_tab

    def test_session_default_name_resets_per_workspace_after_all_sessions_are_closed(self) -> None:
        first = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        second = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(2),
        )

        self.assertEqual("S1", first.display_name)
        self.assertEqual("S2", second.display_name)

        self.session_manager.close_session(first.session_tab_id, when=_dt(3))
        self.session_manager.close_session(second.session_tab_id, when=_dt(4))

        reopened = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(5),
        )

        self.assertEqual("S1", reopened.display_name)

    def test_normal_and_preset_sessions_share_name_counter_and_update_active_session(self) -> None:
        first = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        second = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(2),
        )
        preset = self.session_manager.open_preset_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(3),
        )
        next_normal = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(4),
        )

        self.assertEqual(("S1", "S2", "P3", "S4"), (
            first.display_name,
            second.display_name,
            preset.display_name,
            next_normal.display_name,
        ))
        self.assertEqual(SessionTabKind.NORMAL, first.kind)
        self.assertEqual(SessionTabKind.PRESET, preset.kind)
        self.assertEqual((0, 1, 2, 3), (
            first.sort_order,
            second.sort_order,
            preset.sort_order,
            next_normal.sort_order,
        ))
        self.assertEqual(
            next_normal.session_tab_id,
            self.workspace_manager.get_workspace_tab(
                self.workspace_tab.workspace_tab_id
            ).active_session_tab_id,
        )

    def test_preset_candidate_sessions_use_parent_name_and_insert_after_parent(self) -> None:
        first = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        preset = self.session_manager.open_preset_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(2),
        )
        trailing = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(3),
        )

        first_candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(4),
        )
        second_candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(5),
        )
        next_normal = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(6),
        )

        session_tabs = self.session_manager.list_session_tabs(
            workspace_tab_id=self.workspace_tab.workspace_tab_id,
            include_closed=False,
        )

        self.assertEqual(
            ("S1", "P2", "P2-1", "P2-2", "S3", "S4"),
            tuple(tab.display_name for tab in session_tabs),
        )
        self.assertEqual(
            (0, 1, 2, 3, 4, 5),
            tuple(tab.sort_order for tab in session_tabs),
        )
        self.assertEqual(SessionTabKind.NORMAL, first.kind)
        self.assertEqual(SessionTabKind.PRESET_CANDIDATE, first_candidate.kind)
        self.assertEqual(preset.session_tab_id, first_candidate.parent_session_tab_id)
        self.assertEqual(1, first_candidate.candidate_index)
        self.assertEqual(preset.session_tab_id, second_candidate.parent_session_tab_id)
        self.assertEqual(2, second_candidate.candidate_index)
        self.assertEqual(
            "S3",
            self.session_manager.get_session_tab(trailing.session_tab_id).display_name,
        )
        self.assertEqual(
            next_normal.session_tab_id,
            self.workspace_manager.get_workspace_tab(
                self.workspace_tab.workspace_tab_id
            ).active_session_tab_id,
        )

    def test_name_counter_resets_only_after_every_session_kind_is_closed(self) -> None:
        normal = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        preset = self.session_manager.open_preset_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(2),
        )
        candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(3),
        )

        self.session_manager.close_session(normal.session_tab_id, when=_dt(4))
        self.session_manager.close_session(preset.session_tab_id, when=_dt(5))

        while_candidate_open = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(6),
        )
        self.assertEqual("S3", while_candidate_open.display_name)

        self.session_manager.close_session(candidate.session_tab_id, when=_dt(7))
        self.session_manager.close_session(while_candidate_open.session_tab_id, when=_dt(8))

        reset_preset = self.session_manager.open_preset_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(9),
        )

        self.assertEqual("P1", reset_preset.display_name)
        self.assertEqual(
            reset_preset.session_tab_id,
            self.workspace_manager.get_workspace_tab(
                self.workspace_tab.workspace_tab_id
            ).active_session_tab_id,
        )

    def test_visible_sort_order_resets_after_every_session_kind_is_closed(self) -> None:
        normal = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        preset = self.session_manager.open_preset_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(2),
        )
        candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(3),
        )

        self.session_manager.close_session(normal.session_tab_id, when=_dt(4))
        self.session_manager.close_session(preset.session_tab_id, when=_dt(5))
        self.session_manager.close_session(candidate.session_tab_id, when=_dt(6))

        reset_normal = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(7),
        )
        reset_preset = self.session_manager.open_preset_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(8),
        )

        session_tabs = self.session_manager.list_session_tabs(
            workspace_tab_id=self.workspace_tab.workspace_tab_id,
            include_closed=False,
        )
        self.assertEqual(
            ("S1", "P2"),
            tuple(tab.display_name for tab in session_tabs),
        )
        self.assertEqual(
            (0, 1),
            (reset_normal.sort_order, reset_preset.sort_order),
        )

    def test_completed_sessions_are_sorted_by_recent_activity(self) -> None:
        session1 = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        session2 = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(2),
        )

        self.session_manager.assign_session_id(session1.session_tab_id, "session-1", when=_dt(3))
        self.session_manager.assign_session_id(session2.session_tab_id, "session-2", when=_dt(4))

        self.session_manager.record_completed_turn(
            session1.session_tab_id,
            prompt_text="first prompt",
            response_text="first response",
            started_at=_dt(5),
            completed_at=_dt(6),
            last_activity_at=_dt(6),
        )
        self.session_manager.record_completed_turn(
            session2.session_tab_id,
            prompt_text="second prompt",
            response_text="second response",
            started_at=_dt(7),
            completed_at=_dt(8),
            last_activity_at=_dt(8),
        )
        completed_sessions = self.session_manager.list_completed_sessions(
            self.workspace_tab.workspace_path
        )

        self.assertEqual(("session-2", "session-1"), tuple(item.session_id for item in completed_sessions))

    def test_completed_session_summary_returns_requested_session_only(self) -> None:
        session1 = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        session2 = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(2),
        )
        self.session_manager.assign_session_id(session1.session_tab_id, "session-1", when=_dt(3))
        self.session_manager.assign_session_id(session2.session_tab_id, "session-2", when=_dt(4))

        self.session_manager.record_completed_turn(
            session1.session_tab_id,
            job_id="job-later",
            prompt_text="later prompt",
            response_text="later response",
            started_at=_dt(7),
            completed_at=_dt(8),
            last_activity_at=_dt(8),
        )
        self.session_manager.record_completed_turn(
            session2.session_tab_id,
            job_id="job-other",
            prompt_text="other prompt",
            response_text="other response",
            started_at=_dt(9),
            completed_at=_dt(10),
            last_activity_at=_dt(10),
        )
        self.session_manager.record_completed_turn(
            session1.session_tab_id,
            job_id="job-earlier",
            prompt_text="earlier prompt",
            response_text="earlier response",
            started_at=_dt(5),
            completed_at=_dt(6),
            last_activity_at=_dt(6),
        )

        summary = self.session_manager.get_completed_session_summary(
            self.workspace_tab.workspace_path,
            "session-1",
        )

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual("session-1", summary.session_id)
        self.assertEqual(session1.session_tab_id, summary.session_tab_id)
        self.assertEqual(2, summary.turn_count)
        self.assertEqual(_dt(8), summary.last_activity_at)
        self.assertEqual(
            ("job-earlier", "job-later"),
            tuple(turn.job_id for turn in summary.turns),
        )

    def test_started_turn_is_visible_before_session_id_is_confirmed(self) -> None:
        session = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )

        self.session_manager.record_started_turn(
            session.session_tab_id,
            job_id="job-1",
            prompt_text="starting prompt",
            started_at=_dt(2),
        )

        turns = self.session_manager.list_session_tab_turns(session.session_tab_id)
        self.assertEqual(1, len(turns))
        self.assertEqual("starting prompt", turns[0].prompt_text)
        self.assertIsNone(turns[0].session_id)
        self.assertIsNone(turns[0].response_text)
        self.assertIsNone(turns[0].completed_at)

        self.session_manager.assign_session_id(session.session_tab_id, "session-1", when=_dt(3))

        updated_turns = self.session_manager.list_session_tab_turns(session.session_tab_id)
        self.assertEqual("session-1", updated_turns[0].session_id)

    def test_completed_turn_updates_existing_started_turn(self) -> None:
        session = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        self.session_manager.record_started_turn(
            session.session_tab_id,
            job_id="job-1",
            prompt_text="prompt",
            started_at=_dt(2),
        )
        self.session_manager.assign_session_id(session.session_tab_id, "session-1", when=_dt(3))
        started_snapshot = self.session_manager.list_session_tab_turns(session.session_tab_id)

        self.session_manager.record_completed_turn(
            session.session_tab_id,
            job_id="job-1",
            prompt_text="prompt",
            response_text="response",
            started_at=_dt(2),
            completed_at=_dt(4),
            last_activity_at=_dt(4),
        )

        turns = self.session_manager.list_session_tab_turns(session.session_tab_id)
        self.assertIsNot(started_snapshot, turns)
        self.assertEqual(1, len(turns))
        self.assertEqual("response", turns[0].response_text)
        self.assertEqual(_dt(4), turns[0].completed_at)
        completed_sessions = self.session_manager.list_completed_sessions(
            self.workspace_tab.workspace_path
        )
        self.assertEqual(1, len(completed_sessions))
        self.assertEqual(("response",), tuple(turn.response_text for turn in completed_sessions[0].turns))

    def test_session_tab_turn_snapshot_is_reused_until_turn_history_changes(self) -> None:
        session = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        self.session_manager.assign_session_id(session.session_tab_id, "session-1", when=_dt(2))
        self.session_manager.record_completed_turn(
            session.session_tab_id,
            job_id="job-2",
            prompt_text="later",
            response_text="later response",
            started_at=_dt(5),
            completed_at=_dt(6),
        )
        self.session_manager.record_completed_turn(
            session.session_tab_id,
            job_id="job-1",
            prompt_text="earlier",
            response_text="earlier response",
            started_at=_dt(3),
            completed_at=_dt(4),
        )

        first_snapshot = self.session_manager.list_session_tab_turns(session.session_tab_id)
        second_snapshot = self.session_manager.list_session_tab_turns(session.session_tab_id)

        self.assertIs(first_snapshot, second_snapshot)
        self.assertEqual(("job-1", "job-2"), tuple(turn.job_id for turn in first_snapshot))

        self.session_manager.record_started_turn(
            session.session_tab_id,
            job_id="job-3",
            prompt_text="running",
            started_at=_dt(7),
        )
        updated_snapshot = self.session_manager.list_session_tab_turns(session.session_tab_id)

        self.assertIsNot(first_snapshot, updated_snapshot)
        self.assertEqual(
            ("job-1", "job-2", "job-3"),
            tuple(turn.job_id for turn in updated_snapshot),
        )

    def test_completed_session_history_remains_after_close(self) -> None:
        session = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        self.session_manager.assign_session_id(session.session_tab_id, "session-1", when=_dt(2))
        self.session_manager.record_completed_turn(
            session.session_tab_id,
            prompt_text="hello",
            response_text="world",
            started_at=_dt(3),
            completed_at=_dt(4),
            last_activity_at=_dt(4),
        )

        self.session_manager.close_session(session.session_tab_id, when=_dt(5))
        self.assertEqual(
            ("world",),
            tuple(
                turn.response_text
                for turn in self.session_manager.list_session_turns(
                    self.workspace_tab.workspace_path,
                    "session-1",
                )
            ),
        )


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_manager = WorkspaceManager()
        self.session_manager = SessionManager(self.workspace_manager)
        self.executor = _FakeExecutor()
        self.scheduler = Scheduler(
            workspace_manager=self.workspace_manager,
            session_manager=self.session_manager,
            executor=self.executor,
            settings_provider=lambda: AppSettings(
                executable_path=r"C:\Tools\agent.exe",
            ),
        )
        self.workspace_tab = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Alpha",
            when=_dt(0),
        ).workspace_tab
        self.session_a = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        self.session_b = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(2),
        )

    def test_job_registration_keeps_session_without_confirmed_session_id(self) -> None:
        job = self.scheduler.register_job(self.session_a.session_tab_id, "first prompt", when=_dt(3))

        self.assertEqual(self.session_a.session_tab_id, job.session_tab_id)
        self.assertIsNone(self.session_manager.get_session_tab(self.session_a.session_tab_id).session_id)
        self.assertEqual(JobStatus.QUEUED, job.status)

    def test_force_fresh_session_job_starts_without_existing_session_id(self) -> None:
        self.session_manager.assign_session_id(
            self.session_a.session_tab_id,
            "thread-parent",
            when=_dt(3),
        )
        job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "internal isolated prompt",
            when=_dt(4),
            force_fresh_session=True,
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        self.assertTrue(self.scheduler.get_job(job.job_id).force_fresh_session)
        self.assertEqual(1, len(self.executor.launched_requests))
        self.assertIsNone(self.executor.launched_requests[0].session_id)

    def test_start_and_stop_queue_keeps_running_job_until_completion_event_arrives(self) -> None:
        job = self.scheduler.register_job(self.session_a.session_tab_id, "run me", when=_dt(3))

        started_state = self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        self.assertEqual(QueueStatus.STARTED, started_state.status)
        self.assertEqual(
            job.job_id,
            self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).running_job_id,
        )
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(job.job_id).status)
        self.assertEqual(("run me",), tuple(request.prompt for request in self.executor.launched_requests))

        stopped_state = self.scheduler.stop_queue(
            self.workspace_tab.workspace_tab_id,
            reason=QueueStopReason.USER_STOPPED,
            when=_dt(4),
        )

        self.assertEqual(QueueStatus.STOPPED, stopped_state.status)
        self.assertEqual(QueueStopReason.USER_STOPPED, stopped_state.last_stop_reason)
        self.assertEqual(
            job.job_id,
            self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).running_job_id,
        )
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(job.job_id).status)
        self.assertEqual(("job-1",), tuple(handle.handle_id for handle in self.executor.canceled_handles))

    def test_new_execution_request_uses_latest_timeout_settings_without_mutating_running_request(
        self,
    ) -> None:
        current_settings = AppSettings(
            executable_path=r"C:\Tools\agent.exe",
            execution_timeout_minutes=120,
            inactivity_timeout_minutes=30,
            termination_grace_seconds=5,
        )
        workspace_manager = WorkspaceManager()
        session_manager = SessionManager(workspace_manager)
        executor = _FakeExecutor()
        scheduler = Scheduler(
            workspace_manager=workspace_manager,
            session_manager=session_manager,
            executor=executor,
            settings_provider=lambda: current_settings,
        )
        workspace_tab = workspace_manager.open_validated_workspace(
            r"C:\Repo\Timeouts",
            when=_dt(0),
        ).workspace_tab
        session_tab = session_manager.open_session(
            workspace_tab.workspace_tab_id,
            when=_dt(1),
        )

        first_job = scheduler.register_job(
            session_tab.session_tab_id,
            "first",
            when=_dt(2),
        )
        scheduler.start_queue(workspace_tab.workspace_tab_id)

        self.assertEqual(1, len(executor.launched_requests))
        first_request = executor.launched_requests[0]
        self.assertEqual(120, first_request.operational_settings.execution_timeout_minutes)
        self.assertEqual(30, first_request.operational_settings.inactivity_timeout_minutes)
        self.assertEqual(5, first_request.operational_settings.termination_grace_seconds)

        current_settings = AppSettings(
            executable_path=r"C:\Tools\agent.exe",
            execution_timeout_minutes=0,
            inactivity_timeout_minutes=45,
            termination_grace_seconds=9,
        )
        second_job = scheduler.register_job(
            session_tab.session_tab_id,
            "second",
            when=_dt(3),
        )

        self.assertEqual(1, len(executor.launched_requests))
        self.assertEqual(120, first_request.operational_settings.execution_timeout_minutes)
        self.assertEqual(30, first_request.operational_settings.inactivity_timeout_minutes)
        self.assertEqual(5, first_request.operational_settings.termination_grace_seconds)

        scheduler.complete_running_job(first_job.job_id, when=_dt(4))

        self.assertEqual(2, len(executor.launched_requests))
        second_request = executor.launched_requests[1]
        self.assertEqual(second_job.job_id, second_request.job_id)
        self.assertEqual(0, second_request.operational_settings.execution_timeout_minutes)
        self.assertEqual(45, second_request.operational_settings.inactivity_timeout_minutes)
        self.assertEqual(9, second_request.operational_settings.termination_grace_seconds)

    def test_execution_request_uses_registered_agent_options_with_latest_runtime_controls(
        self,
    ) -> None:
        current_settings = AppSettings(
            agent_provider="codex",
            executable_paths={
                "codex": r"C:\Tools\codex.exe",
                "pi": r"C:\Tools\pi.exe",
            },
            execution_timeout_minutes=120,
        )
        workspace_manager = WorkspaceManager()
        session_manager = SessionManager(workspace_manager)
        executor = _FakeExecutor()
        scheduler = Scheduler(
            workspace_manager=workspace_manager,
            session_manager=session_manager,
            executor=executor,
            settings_provider=lambda: current_settings,
        )
        workspace_tab = workspace_manager.open_validated_workspace(
            r"C:\Repo\Snapshot",
            when=_dt(0),
        ).workspace_tab
        session_tab = session_manager.open_session(
            workspace_tab.workspace_tab_id,
            when=_dt(1),
        )

        job = scheduler.register_job(
            session_tab.session_tab_id,
            "snapshot",
            when=_dt(2),
            execution_options=AgentExecutionOptions(
                agent_provider="codex",
                model="gpt-5.4",
                reasoning_effort="high",
            ),
        )
        current_settings = AppSettings(
            agent_provider="pi",
            executable_paths={
                "codex": r"C:\Tools\codex-updated.exe",
                "pi": r"C:\Tools\pi.exe",
            },
            execution_timeout_minutes=15,
        )

        scheduler.start_queue(workspace_tab.workspace_tab_id)

        self.assertEqual(1, len(executor.launched_requests))
        request = executor.launched_requests[0]
        self.assertEqual(job.job_id, request.job_id)
        self.assertEqual("codex", request.operational_settings.agent_provider)
        self.assertEqual("gpt-5.4", request.execution_options.model)
        self.assertEqual("high", request.execution_options.reasoning_effort)
        self.assertEqual(r"C:\Tools\codex-updated.exe", request.operational_settings.executable_path)
        self.assertEqual(15, request.operational_settings.execution_timeout_minutes)

    def test_waiting_for_configuration_job_is_preserved_and_skipped(self) -> None:
        self.executor.blocked_prompts.add("needs-config")
        waiting_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "needs-config",
            when=_dt(3),
        )
        running_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "ready-now",
            when=_dt(4),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        self.assertEqual(
            JobStatus.WAITING_FOR_CONFIGURATION,
            self.scheduler.get_job(waiting_job.job_id).status,
        )
        self.assertEqual(
            "설정 확인 필요",
            self.scheduler.get_job(waiting_job.job_id).configuration_wait_reason,
        )
        self.assertEqual(
            JobStatus.RUNNING,
            self.scheduler.get_job(running_job.job_id).status,
        )
        self.assertEqual(
            running_job.job_id,
            self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).running_job_id,
        )

    def test_registered_older_other_session_job_runs_before_same_session_follow_up(self) -> None:
        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a first",
            when=_dt(3),
        )
        other_session_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b first",
            when=_dt(4),
        )
        follow_up_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a second",
            when=_dt(5),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.complete_running_job(first_job.job_id, when=_dt(6))

        self.assertEqual(JobStatus.COMPLETED, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(follow_up_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(other_session_job.job_id).status)
        self.assertEqual(
            ("session-a first", "session-b first"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_queue_stops_when_workspace_task_list_is_all_completed(self) -> None:
        job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "single task",
            when=_dt(3),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        completed_job = self.scheduler.complete_running_job(job.job_id, when=_dt(4))
        queue_state = self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id)

        self.assertEqual(JobStatus.COMPLETED, completed_job.status)
        self.assertEqual(QueueStatus.STOPPED, queue_state.status)
        self.assertIsNone(queue_state.running_job_id)
        self.assertEqual(QueueStopReason.ALL_JOBS_COMPLETED, queue_state.last_stop_reason)

    def test_waiting_for_configuration_job_keeps_queue_started_after_other_jobs_complete(self) -> None:
        self.executor.blocked_prompts.add("needs-config")
        waiting_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "needs-config",
            when=_dt(3),
        )
        running_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "ready-now",
            when=_dt(4),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        self.scheduler.complete_running_job(running_job.job_id, when=_dt(5))
        queue_state = self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id)

        self.assertEqual(
            JobStatus.WAITING_FOR_CONFIGURATION,
            self.scheduler.get_job(waiting_job.job_id).status,
        )
        self.assertEqual(QueueStatus.STARTED, queue_state.status)
        self.assertIsNone(queue_state.running_job_id)

    def test_job_registration_preserves_pending_queue_order(self) -> None:
        self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a first",
            when=_dt(3),
        )
        self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b first",
            when=_dt(4),
        )
        self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b second",
            when=_dt(5),
        )
        self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a second",
            when=_dt(6),
        )

        jobs = self.scheduler.list_jobs()

        self.assertEqual(
            (
                "session-a first",
                "session-b first",
                "session-b second",
                "session-a second",
            ),
            tuple(job.prompt for job in jobs),
        )
        self.assertEqual((1, 2, 3, 4), tuple(job.queue_order for job in jobs))

    def test_snapshot_jobs_by_id_does_not_use_queue_order_sorting(self) -> None:
        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a first",
            when=_dt(3),
        )
        second_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b first",
            when=_dt(4),
        )

        with patch(
            "app.scheduler._job_list_order_key",
            side_effect=AssertionError("snapshot should not sort jobs"),
        ):
            snapshot = self.scheduler.snapshot_jobs_by_id()

        self.assertEqual(
            {first_job.job_id, second_job.job_id},
            set(snapshot),
        )
        self.assertIs(self.scheduler.get_job(first_job.job_id), snapshot[first_job.job_id])
        self.assertIs(self.scheduler.get_job(second_job.job_id), snapshot[second_job.job_id])

    def test_list_jobs_by_workspace_groups_requested_workspaces_in_queue_order(self) -> None:
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(7),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(8),
        )
        workspace_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a first",
            when=_dt(9),
        )
        other_workspace_job = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b first",
            when=_dt(10),
        )
        second_workspace_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "workspace-a second",
            when=_dt(11),
        )
        second_other_workspace_job = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b second",
            when=_dt(12),
        )

        jobs_by_workspace = self.scheduler.list_jobs_by_workspace(
            (other_workspace.workspace_tab_id, self.workspace_tab.workspace_tab_id)
        )

        self.assertEqual(
            (other_workspace.workspace_tab_id, self.workspace_tab.workspace_tab_id),
            tuple(jobs_by_workspace),
        )
        self.assertEqual(
            (other_workspace_job.job_id, second_other_workspace_job.job_id),
            tuple(job.job_id for job in jobs_by_workspace[other_workspace.workspace_tab_id]),
        )
        self.assertEqual(
            (workspace_job.job_id, second_workspace_job.job_id),
            tuple(job.job_id for job in jobs_by_workspace[self.workspace_tab.workspace_tab_id]),
        )

    def test_summarize_workspace_jobs_reports_presence_without_sorting(self) -> None:
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(7),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(8),
        )
        self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a running",
            when=_dt(9),
        )
        self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b queued",
            when=_dt(10),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        with patch(
            "app.scheduler._job_list_order_key",
            side_effect=AssertionError("workspace job summary should not sort jobs"),
        ):
            summaries = self.scheduler.summarize_workspace_jobs(
                (
                    other_workspace.workspace_tab_id,
                    self.workspace_tab.workspace_tab_id,
                    "workspace-empty",
                )
            )
            workspace_has_jobs = self.scheduler.workspace_has_jobs(
                self.workspace_tab.workspace_tab_id
            )
            empty_workspace_has_jobs = self.scheduler.workspace_has_jobs(
                "workspace-empty"
            )

        self.assertEqual(
            (
                other_workspace.workspace_tab_id,
                self.workspace_tab.workspace_tab_id,
                "workspace-empty",
            ),
            tuple(summaries),
        )
        self.assertTrue(summaries[self.workspace_tab.workspace_tab_id].has_jobs)
        self.assertTrue(
            summaries[self.workspace_tab.workspace_tab_id].has_running_job
        )
        self.assertTrue(summaries[other_workspace.workspace_tab_id].has_jobs)
        self.assertFalse(summaries[other_workspace.workspace_tab_id].has_running_job)
        self.assertFalse(summaries["workspace-empty"].has_jobs)
        self.assertFalse(summaries["workspace-empty"].has_running_job)
        self.assertTrue(workspace_has_jobs)
        self.assertFalse(empty_workspace_has_jobs)

    def test_s1_p2_s3_registration_keeps_queue_order(self) -> None:
        workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\QueueOrder",
            when=_dt(3),
        ).workspace_tab
        first_session = self.session_manager.open_session(
            workspace.workspace_tab_id,
            when=_dt(4),
        )
        preset_session = self.session_manager.open_preset_session(
            workspace.workspace_tab_id,
            when=_dt(5),
        )
        second_session = self.session_manager.open_session(
            workspace.workspace_tab_id,
            when=_dt(6),
        )

        self.assertEqual(("S1", "P2", "S3"), (
            first_session.display_name,
            preset_session.display_name,
            second_session.display_name,
        ))

        self.scheduler.register_job(first_session.session_tab_id, "first", when=_dt(7))
        self.scheduler.register_job(preset_session.session_tab_id, "preset", when=_dt(8))
        self.scheduler.register_job(second_session.session_tab_id, "second", when=_dt(9))

        workspace_jobs = tuple(
            job
            for job in self.scheduler.list_jobs()
            if job.workspace_tab_id == workspace.workspace_tab_id
        )
        session_names_by_id = {
            tab.session_tab_id: tab.display_name
            for tab in self.session_manager.list_session_tabs(
                workspace_tab_id=workspace.workspace_tab_id
            )
        }

        self.assertEqual(
            ("S1", "P2", "S3"),
            tuple(session_names_by_id[job.session_tab_id] for job in workspace_jobs),
        )
        self.assertEqual(("first", "preset", "second"), tuple(job.prompt for job in workspace_jobs))
        self.assertEqual((1, 2, 3), tuple(job.queue_order for job in workspace_jobs))

    def test_running_session_pending_jobs_keep_registration_order(self) -> None:
        running_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a running",
            when=_dt(3),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b waiting",
            when=_dt(4),
        )
        self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a follow-up",
            when=_dt(5),
        )

        jobs = self.scheduler.list_jobs()

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(running_job.job_id).status)
        self.assertEqual(
            ("session-a running", "session-b waiting", "session-a follow-up"),
            tuple(job.prompt for job in jobs),
        )
        self.assertEqual((1, 2, 3), tuple(job.queue_order for job in jobs))

    def test_deferred_completion_dispatch_preserves_registration_order(self) -> None:
        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a first",
            when=_dt(3),
        )
        other_session_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b first",
            when=_dt(4),
        )
        follow_up_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a second",
            when=_dt(5),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        with self.scheduler.defer_dispatch():
            self.scheduler.complete_running_job(first_job.job_id, when=_dt(6))

        self.assertTrue(self.scheduler.has_pending_dispatch())
        self.assertEqual(JobStatus.COMPLETED, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(follow_up_job.job_id).status)

        self.scheduler.dispatch_next_job()

        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(follow_up_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(other_session_job.job_id).status)
        self.assertEqual(
            ("session-a first", "session-b first"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_prioritized_queued_jobs_run_before_older_pending_jobs(self) -> None:
        preset = self.session_manager.open_preset_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(3),
        )
        generation_job = self.scheduler.register_job(
            preset.session_tab_id,
            "preset work generation",
            when=_dt(4),
        )
        existing_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "existing queued",
            when=_dt(5),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        with self.scheduler.defer_dispatch():
            self.scheduler.complete_running_job(generation_job.job_id, when=_dt(6))
        first_candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(7),
        )
        second_candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(8),
        )
        with self.scheduler.defer_dispatch():
            first_candidate_job = self.scheduler.register_job(
                first_candidate.session_tab_id,
                "candidate one",
                when=_dt(9),
            )
            second_candidate_job = self.scheduler.register_job(
                second_candidate.session_tab_id,
                "candidate two",
                when=_dt(10),
            )
            self.scheduler.prioritize_queued_jobs(
                (first_candidate_job.job_id, second_candidate_job.job_id)
            )

        self.scheduler.dispatch_next_job()

        self.assertEqual(
            (
                "preset work generation",
                "candidate one",
                "candidate two",
                "existing queued",
            ),
            tuple(job.prompt for job in self.scheduler.list_jobs()),
        )
        self.assertEqual(
            JobStatus.RUNNING,
            self.scheduler.get_job(first_candidate_job.job_id).status,
        )
        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(existing_job.job_id).status)
        self.assertEqual(
            ("preset work generation", "candidate one"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_prioritized_candidate_commit_jobs_override_deferred_parent_follow_up(self) -> None:
        preset = self.session_manager.open_preset_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(3),
        )
        generation_job = self.scheduler.register_job(
            preset.session_tab_id,
            "preset work generation",
            when=_dt(4),
        )
        parent_follow_up = self.scheduler.register_job(
            preset.session_tab_id,
            "unexpected parent follow-up",
            when=_dt(5),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        with self.scheduler.defer_dispatch():
            self.scheduler.complete_running_job(generation_job.job_id, when=_dt(6))

        first_candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(7),
        )
        second_candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(8),
        )
        with self.scheduler.defer_dispatch():
            first_candidate_job = self.scheduler.register_job(
                first_candidate.session_tab_id,
                "candidate one",
                when=_dt(9),
            )
            first_candidate_commit = self.scheduler.register_job(
                first_candidate.session_tab_id,
                "commit candidate one",
                when=_dt(10),
            )
            second_candidate_job = self.scheduler.register_job(
                second_candidate.session_tab_id,
                "candidate two",
                when=_dt(11),
            )
            second_candidate_commit = self.scheduler.register_job(
                second_candidate.session_tab_id,
                "commit candidate two",
                when=_dt(12),
            )
            self.scheduler.prioritize_queued_jobs(
                (
                    first_candidate_job.job_id,
                    first_candidate_commit.job_id,
                    second_candidate_job.job_id,
                    second_candidate_commit.job_id,
                )
            )

        self.scheduler.dispatch_next_job()
        self.scheduler.complete_running_job(first_candidate_job.job_id, when=_dt(13))
        self.scheduler.complete_running_job(first_candidate_commit.job_id, when=_dt(14))
        self.scheduler.complete_running_job(second_candidate_job.job_id, when=_dt(15))

        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(parent_follow_up.job_id).status)
        self.assertEqual(
            (
                "preset work generation",
                "candidate one",
                "commit candidate one",
                "candidate two",
                "commit candidate two",
            ),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_started_workspace_queues_run_independently(self) -> None:
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(3),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(4),
        )

        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a",
            when=_dt(5),
        )
        second_job = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b",
            when=_dt(6),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.start_queue(other_workspace.workspace_tab_id)

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            second_job.job_id,
            self.scheduler.get_queue_state(other_workspace.workspace_tab_id).running_job_id,
        )

        stopped_state = self.scheduler.stop_queue(
            self.workspace_tab.workspace_tab_id,
            reason=QueueStopReason.USER_STOPPED,
            when=_dt(7),
        )

        self.assertEqual(QueueStatus.STOPPED, stopped_state.status)
        self.assertEqual(QueueStopReason.USER_STOPPED, stopped_state.last_stop_reason)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            first_job.job_id,
            self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).running_job_id,
        )
        self.assertEqual(
            second_job.job_id,
            self.scheduler.get_queue_state(other_workspace.workspace_tab_id).running_job_id,
        )
        self.assertEqual(
            ("workspace-a", "workspace-b"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_dispatch_can_exclude_workspace_with_pending_follow_up(self) -> None:
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(3),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(4),
        )

        first_a = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a first",
            when=_dt(5),
        )
        second_a = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a second",
            when=_dt(6),
        )
        first_b = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b first",
            when=_dt(7),
        )
        second_b = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b second",
            when=_dt(8),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.start_queue(other_workspace.workspace_tab_id)
        with self.scheduler.defer_dispatch():
            self.scheduler.complete_running_job(first_a.job_id, when=_dt(9))
            self.scheduler.complete_running_job(first_b.job_id, when=_dt(10))

        self.scheduler.dispatch_next_job(
            excluded_workspace_tab_ids=(self.workspace_tab.workspace_tab_id,)
        )

        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(second_a.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_b.job_id).status)
        self.assertTrue(self.scheduler.has_pending_dispatch())
        self.assertEqual(
            (self.workspace_tab.workspace_tab_id,),
            self.scheduler.pending_dispatch_workspace_tab_ids(),
        )

        self.scheduler.dispatch_next_job()

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_a.job_id).status)
        self.assertFalse(self.scheduler.has_pending_dispatch())
        self.assertEqual(
            (
                "workspace-a first",
                "workspace-b first",
                "workspace-b second",
                "workspace-a second",
            ),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_dispatch_reuses_job_scan_when_filling_multiple_workspace_slots(self) -> None:
        workspace_tabs = [self.workspace_tab]
        session_tabs = [self.session_a]
        for offset, workspace_name in enumerate(("Beta", "Gamma", "Delta"), start=1):
            workspace_tab = self.workspace_manager.open_validated_workspace(
                rf"C:\Repo\{workspace_name}",
                when=_dt(3 + offset),
            ).workspace_tab
            workspace_tabs.append(workspace_tab)
            session_tabs.append(
                self.session_manager.open_session(
                    workspace_tab.workspace_tab_id,
                    when=_dt(7 + offset),
                )
            )

        first_jobs = []
        for workspace_index, session_tab in enumerate(session_tabs):
            first_job = self.scheduler.register_job(
                session_tab.session_tab_id,
                f"workspace-{workspace_index}-first",
                when=_dt(20 + workspace_index),
            )
            first_jobs.append(first_job)
            for job_index in range(1, 6):
                self.scheduler.register_job(
                    session_tab.session_tab_id,
                    f"workspace-{workspace_index}-{job_index}",
                    when=_dt(30 + workspace_index + job_index),
                )

        with self.scheduler.defer_dispatch():
            for workspace_tab in workspace_tabs:
                self.scheduler.start_queue(workspace_tab.workspace_tab_id)

        counting_jobs = _CountingJobDict(self.scheduler._jobs)
        self.scheduler._jobs = counting_jobs

        self.scheduler.dispatch_next_job()

        self.assertEqual(1, counting_jobs.values_calls)
        self.assertEqual(
            tuple(job.job_id for job in first_jobs),
            tuple(request.job_id for request in self.executor.launched_requests),
        )
        self.assertEqual(
            tuple(job.job_id for job in first_jobs),
            tuple(
                self.scheduler.get_queue_state(workspace_tab.workspace_tab_id).running_job_id
                for workspace_tab in workspace_tabs
            ),
        )
        self.assertTrue(
            all(
                self.scheduler.get_job(job.job_id).status == JobStatus.RUNNING
                for job in first_jobs
            )
        )

    def test_stop_all_queues_cancels_running_jobs_for_all_workspaces(self) -> None:
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(3),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(4),
        )

        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a",
            when=_dt(5),
        )
        second_job = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b",
            when=_dt(6),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.start_queue(other_workspace.workspace_tab_id)

        states = self.scheduler.stop_all_queues(reason=QueueStopReason.USER_STOPPED)

        self.assertEqual(
            {QueueStatus.STOPPED},
            {state.status for state in states},
        )
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            ("job-1", "job-2"),
            tuple(handle.handle_id for handle in self.executor.canceled_handles),
        )

    def test_delete_job_removes_non_running_job(self) -> None:
        running_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "running",
            when=_dt(3),
        )
        queued_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "queued",
            when=_dt(4),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        deleted_job = self.scheduler.delete_job(queued_job.job_id)

        self.assertEqual(queued_job.job_id, deleted_job.job_id)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(running_job.job_id).status)
        with self.assertRaises(KeyError):
            self.scheduler.get_job(queued_job.job_id)

    def test_delete_job_rejects_running_job(self) -> None:
        job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "running",
            when=_dt(3),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        with self.assertRaisesRegex(ValueError, "Cannot delete a running job"):
            self.scheduler.delete_job(job.job_id)

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(job.job_id).status)


class AppRuntimePollingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = AppRuntime(
            controller=_RuntimeControllerStub(),
            repository=_RuntimeRepositoryStub(),
        )

    def test_drain_events_respects_max_items_and_preserves_remaining_order(self) -> None:
        first = LogAppendedEvent(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            stream_name="progress",
            line="first",
        )
        second = LogAppendedEvent(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            stream_name="progress",
            line="second",
        )
        third = LogAppendedEvent(
            job_id="job-3",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            stream_name="progress",
            line="third",
        )
        for event in (first, second, third):
            self.runtime.event_queue.put(event)

        self.assertEqual((first, second), self.runtime.drain_events(max_items=2))
        self.assertEqual((third,), self.runtime.drain_events())

    def test_disabled_file_logging_keeps_ui_progress_log_events(self) -> None:
        log_event = LogAppendedEvent(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            stream_name="progress",
            line="hidden",
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeEventsControllerStub((log_event,))
        runtime._controller_state_lock = threading.RLock()
        runtime._event_queue = Queue()
        runtime._settings = AppSettings(file_logging_enabled=False)
        runtime._job_progress_logs = {"job-old": ["old"]}

        runtime._sync_controller_events()

        self.assertEqual((log_event,), runtime.drain_events())
        self.assertEqual(("hidden",), runtime.get_job_progress_logs("job-1"))
        self.assertEqual(("old",), runtime.get_job_progress_logs("job-old"))


class AppRuntimeWorkspaceJobListTests(unittest.TestCase):
    def test_list_workspace_jobs_filters_to_workspace_in_scheduler_order(self) -> None:
        first_workspace_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="first",
            queue_order=1,
        )
        other_workspace_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-b",
            session_tab_id="session-b",
            prompt="other",
            queue_order=2,
        )
        second_workspace_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="second",
            queue_order=3,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspaceJobControllerStub(
            (first_workspace_job, other_workspace_job, second_workspace_job)
        )

        jobs = runtime.list_workspace_jobs("workspace-a")

        self.assertEqual(("job-1", "job-3"), tuple(job.job_id for job in jobs))
        self.assertEqual(
            ["workspace-a"],
            runtime._controller.workspace_manager.requested_workspace_tab_ids,
        )

    def test_list_jobs_by_workspace_uses_scheduler_grouped_lookup(self) -> None:
        first_workspace_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="first",
            queue_order=1,
        )
        other_workspace_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-b",
            session_tab_id="session-b",
            prompt="other",
            queue_order=2,
        )
        second_workspace_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="second",
            queue_order=3,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspaceJobControllerStub(
            (first_workspace_job, other_workspace_job, second_workspace_job)
        )

        jobs_by_workspace = runtime.list_jobs_by_workspace(("workspace-a", "workspace-b"))

        self.assertEqual(
            ("job-1", "job-3"),
            tuple(job.job_id for job in jobs_by_workspace["workspace-a"]),
        )
        self.assertEqual(
            ("job-2",),
            tuple(job.job_id for job in jobs_by_workspace["workspace-b"]),
        )
        self.assertEqual(
            ["workspace-a", "workspace-b"],
            runtime._controller.workspace_manager.requested_workspace_tab_ids,
        )
        self.assertEqual(
            [("workspace-a", "workspace-b")],
            runtime._controller.scheduler.list_jobs_by_workspace_requests,
        )
        self.assertEqual(0, runtime._controller.scheduler.list_jobs_calls)

    def test_summarize_workspace_jobs_uses_scheduler_summary_lookup(self) -> None:
        first_workspace_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="first",
            queue_order=1,
            status=JobStatus.RUNNING,
        )
        other_workspace_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-b",
            session_tab_id="session-b",
            prompt="other",
            queue_order=2,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspaceJobControllerStub(
            (first_workspace_job, other_workspace_job)
        )

        summaries = runtime.summarize_workspace_jobs(("workspace-a", "workspace-b"))

        self.assertTrue(summaries["workspace-a"].has_jobs)
        self.assertTrue(summaries["workspace-a"].has_running_job)
        self.assertTrue(summaries["workspace-b"].has_jobs)
        self.assertFalse(summaries["workspace-b"].has_running_job)
        self.assertEqual(
            ["workspace-a", "workspace-b"],
            runtime._controller.workspace_manager.requested_workspace_tab_ids,
        )
        self.assertEqual(
            [("workspace-a", "workspace-b")],
            runtime._controller.scheduler.summarize_workspace_jobs_requests,
        )
        self.assertEqual([], runtime._controller.scheduler.list_workspace_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.list_jobs_by_workspace_requests)
        self.assertEqual(0, runtime._controller.scheduler.list_jobs_calls)

    def test_workspace_has_jobs_uses_scheduler_presence_lookup(self) -> None:
        workspace_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="first",
            queue_order=1,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspaceJobControllerStub((workspace_job,))

        self.assertTrue(runtime.workspace_has_jobs("workspace-a"))

        self.assertEqual(
            ["workspace-a"],
            runtime._controller.workspace_manager.requested_workspace_tab_ids,
        )
        self.assertEqual(
            ["workspace-a"],
            runtime._controller.scheduler.workspace_has_jobs_requests,
        )
        self.assertEqual([], runtime._controller.scheduler.summarize_workspace_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.list_workspace_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.list_jobs_by_workspace_requests)
        self.assertEqual(0, runtime._controller.scheduler.list_jobs_calls)

    def test_workspace_has_runnable_jobs_uses_scheduler_runnable_lookup(self) -> None:
        workspace_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="first",
            queue_order=1,
            status=JobStatus.QUEUED,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspaceJobControllerStub((workspace_job,))

        self.assertTrue(runtime.workspace_has_runnable_jobs("workspace-a"))

        self.assertEqual(
            ["workspace-a"],
            runtime._controller.workspace_manager.requested_workspace_tab_ids,
        )
        self.assertEqual(
            ["workspace-a"],
            runtime._controller.scheduler.workspace_has_runnable_jobs_requests,
        )
        self.assertEqual([], runtime._controller.scheduler.workspace_has_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.summarize_workspace_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.list_workspace_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.list_jobs_by_workspace_requests)
        self.assertEqual(0, runtime._controller.scheduler.list_jobs_calls)

    def test_delete_job_clears_runtime_job_caches(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="delete me",
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeDeleteControllerStub(job)
        runtime._job_progress_logs = {"job-1": ["log"], "job-2": ["keep"]}
        runtime._job_user_messages = {"job-1": "message", "job-2": "keep"}

        deleted_job = runtime.delete_job("job-1")

        self.assertEqual(job, deleted_job)
        self.assertEqual(["job-1"], runtime._controller.deleted_job_ids)
        self.assertNotIn("job-1", runtime._job_progress_logs)
        self.assertNotIn("job-1", runtime._job_user_messages)
        self.assertEqual(["keep"], runtime._job_progress_logs["job-2"])

    def test_workspace_path_has_running_job_matches_open_workspace_path(self) -> None:
        workspace_manager = WorkspaceManager()
        workspace_tab = workspace_manager.open_validated_workspace(
            r"C:\Repo\Alpha",
            when=_dt(1),
        ).workspace_tab
        running_job = Job(
            job_id="job-1",
            workspace_tab_id=workspace_tab.workspace_tab_id,
            session_tab_id="session-a",
            prompt="running",
            status=JobStatus.RUNNING,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspacePathRunningControllerStub(
            workspace_manager,
            (running_job,),
        )

        self.assertTrue(runtime.workspace_path_has_running_job(r"c:/repo/alpha/"))

    def test_workspace_path_has_running_job_ignores_non_running_jobs(self) -> None:
        workspace_manager = WorkspaceManager()
        workspace_tab = workspace_manager.open_validated_workspace(
            r"C:\Repo\Alpha",
            when=_dt(1),
        ).workspace_tab
        queued_job = Job(
            job_id="job-1",
            workspace_tab_id=workspace_tab.workspace_tab_id,
            session_tab_id="session-a",
            prompt="queued",
            status=JobStatus.QUEUED,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspacePathRunningControllerStub(
            workspace_manager,
            (queued_job,),
        )

        self.assertFalse(runtime.workspace_path_has_running_job(r"C:\Repo\Alpha"))


class AppRuntimeDeferredDispatchTests(unittest.TestCase):
    def test_submit_job_runs_deferred_dispatch_on_runtime_worker(self) -> None:
        controller = _RuntimeDispatchControllerStub()
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())
        caller_thread_id = threading.get_ident()

        job = runtime.submit_job("session-1", "prompt")

        self.assertEqual("job-1", job.job_id)
        self.assertEqual([False], controller.submit_dispatch_immediately_values)
        self.assertTrue(
            _wait_until(lambda: bool(controller.dispatch_thread_ids)),
            "deferred submit dispatch did not run",
        )
        self.assertNotEqual(caller_thread_id, controller.dispatch_thread_ids[0])

    def test_deferred_dispatch_holds_controller_state_lock_while_dispatch_runs(
        self,
    ) -> None:
        controller = _RuntimeDispatchControllerStub()
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())
        controller.block_dispatch = True

        runtime.submit_job("session-1", "prompt")

        self.assertTrue(
            controller.dispatch_started.wait(timeout=1.0),
            "deferred dispatch did not run",
        )

        controller_lock = runtime._get_controller_state_lock()
        acquired = controller_lock.acquire(blocking=False)
        if acquired:
            controller_lock.release()

        controller.release_dispatch.set()
        self.assertTrue(
            _wait_until(lambda: not runtime.has_pending_background_work()),
            "deferred dispatch did not finish",
        )
        self.assertFalse(acquired, "deferred dispatch did not hold the controller lock")

    def test_completion_poll_runs_deferred_dispatch_on_runtime_worker(self) -> None:
        controller = _RuntimeDispatchControllerStub(background_events_to_process=1)
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())
        caller_thread_id = threading.get_ident()

        processed = runtime.process_background_events()

        self.assertEqual(1, processed)
        self.assertEqual([False], controller.process_dispatch_immediately_values)
        self.assertTrue(
            _wait_until(lambda: bool(controller.dispatch_thread_ids)),
            "deferred completion dispatch did not run",
        )
        self.assertNotEqual(caller_thread_id, controller.dispatch_thread_ids[0])


class AppRuntimePromptImportTests(unittest.TestCase):
    def test_open_normal_and_preset_sessions_seed_default_ai_options(self) -> None:
        controller = _RuntimeSessionOpenControllerStub()
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = controller
        runtime._controller_state_lock = threading.RLock()
        runtime._settings = AppSettings(
            agent_provider="pi",
            executable_paths={"pi": r"C:\Tools\pi.exe"},
            default_model="pi-pro",
            default_reasoning_effort="high",
        )
        expected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-pro",
            reasoning_effort="high",
        )

        normal_session = runtime.open_session("workspace-1")
        preset_session = runtime.open_preset_session("workspace-1")

        self.assertEqual(
            [expected_execution_options],
            controller.open_session_execution_options,
        )
        self.assertEqual(
            [expected_execution_options],
            controller.open_preset_session_execution_options,
        )
        self.assertEqual(expected_execution_options, normal_session.execution_options)
        self.assertEqual(expected_execution_options, preset_session.execution_options)

    def test_open_sessions_reuse_last_top_execution_options_for_workspace_path(
        self,
    ) -> None:
        controller = _RuntimeSessionOpenControllerStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
                "workspace-3": r"D:\OtherRepo",
            },
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = controller
        runtime._controller_state_lock = threading.RLock()
        runtime._settings = AppSettings(
            agent_provider="codex",
            executable_paths={"codex": "codex", "pi": "pi"},
        )
        selected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-pro",
            reasoning_effort="high",
        )

        first_session = runtime.open_session("workspace-1")
        runtime.set_session_execution_options(
            first_session.session_tab_id,
            selected_execution_options,
        )
        same_workspace_session = runtime.open_session("workspace-2")
        same_workspace_preset = runtime.open_preset_session("workspace-2")
        other_workspace_session = runtime.open_session("workspace-3")

        self.assertEqual(
            selected_execution_options,
            same_workspace_session.execution_options,
        )
        self.assertEqual(
            selected_execution_options,
            same_workspace_preset.execution_options,
        )
        self.assertEqual(
            AgentExecutionOptions(agent_provider="codex"),
            other_workspace_session.execution_options,
        )

    def test_import_prompt_sessions_creates_normal_sessions_and_jobs(self) -> None:
        controller = _RuntimePromptImportControllerStub()
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = controller
        runtime._controller_state_lock = threading.RLock()
        runtime._event_queue = Queue()
        runtime._settings = AppSettings(
            agent_provider="pi",
            executable_paths={"pi": "pi"},
        )
        expected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
        )

        result = runtime.import_prompt_sessions(
            "workspace-1",
            ("first prompt", "second prompt"),
            auto_commit_enabled=True,
        )

        self.assertEqual(
            ["workspace-1", "workspace-1"],
            controller.open_session_workspace_ids,
        )
        self.assertEqual(
            [expected_execution_options, expected_execution_options],
            controller.open_session_execution_options,
        )
        self.assertEqual(
            [
                ("session-1", "first prompt", False),
                ("session-1", AUTO_COMMIT_PROMPT, False),
                ("session-2", "second prompt", False),
                ("session-2", AUTO_COMMIT_PROMPT, False),
            ],
            controller.submitted_jobs,
        )
        self.assertEqual(
            [expected_execution_options] * 4,
            controller.submitted_execution_options,
        )
        self.assertEqual(
            [
                ("session-1", expected_execution_options),
                ("session-2", expected_execution_options),
            ],
            controller.session_manager.locked_execution_options,
        )
        self.assertEqual(
            ("session-1", "session-2"),
            tuple(session.session_tab_id for session in result.session_tabs),
        )
        self.assertEqual(
            ("first prompt", AUTO_COMMIT_PROMPT, "second prompt", AUTO_COMMIT_PROMPT),
            tuple(job.prompt for job in result.registered_jobs),
        )

    def test_import_prompt_sessions_uses_supplied_agent_execution_options(self) -> None:
        controller = _RuntimePromptImportControllerStub()
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = controller
        runtime._controller_state_lock = threading.RLock()
        runtime._event_queue = Queue()
        runtime._settings = AppSettings(
            agent_provider="pi",
            executable_paths={"codex": "codex", "pi": "pi"},
        )
        selected_execution_options = AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4",
            reasoning_effort="high",
        )

        runtime.import_prompt_sessions(
            "workspace-1",
            ("first prompt", "second prompt"),
            auto_commit_enabled=True,
            execution_options=selected_execution_options,
        )

        self.assertEqual(
            [selected_execution_options, selected_execution_options],
            controller.open_session_execution_options,
        )
        self.assertEqual(
            [selected_execution_options] * 4,
            controller.submitted_execution_options,
        )
        self.assertEqual(
            [
                ("session-1", selected_execution_options),
                ("session-2", selected_execution_options),
            ],
            controller.session_manager.locked_execution_options,
        )


class AppRuntimeQueueStartShutdownTests(unittest.TestCase):
    def test_stop_queue_suppresses_overlapped_background_start_completion(self) -> None:
        release_start = threading.Event()
        controller = _BlockingRuntimeQueueControllerStub(release_start=release_start)
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())

        runtime.start_queue_in_background("workspace-1")
        self.assertTrue(
            controller.start_queue_started.wait(timeout=1.0),
            "background queue start did not begin",
        )

        runtime.stop_queue("workspace-1")
        release_start.set()

        self.assertTrue(
            _wait_until(
                lambda: runtime.process_background_events() >= 0
                and not runtime.has_pending_background_work()
            ),
            "overlapped queue start did not finish cleanup",
        )
        events = runtime.drain_events()

        self.assertFalse(any(isinstance(event, QueueStartCompletedEvent) for event in events))
        self.assertEqual(["workspace-1"], controller.started_queue_ids)
        self.assertEqual(["workspace-1", "workspace-1"], controller.stopped_queue_ids)

    def test_close_workspace_suppresses_completed_background_start_event(self) -> None:
        release_start = threading.Event()
        controller = _BlockingRuntimeQueueControllerStub(release_start=release_start)
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())

        runtime.start_queue_in_background("workspace-1")
        self.assertTrue(
            controller.start_queue_started.wait(timeout=1.0),
            "background queue start did not begin",
        )
        release_start.set()
        self.assertTrue(
            _wait_until(lambda: not runtime._runtime_action_completion_queue.empty()),
            "background queue start completion was not queued",
        )

        runtime.close_workspace("workspace-1")
        runtime.process_background_events()
        events = runtime.drain_events()

        self.assertFalse(any(isinstance(event, QueueStartCompletedEvent) for event in events))
        self.assertEqual(["workspace-1"], controller.started_queue_ids)
        self.assertEqual(["workspace-1"], controller.closed_workspace_ids)
        self.assertEqual(["workspace-1"], controller.stopped_queue_ids)

    def test_close_session_suppresses_completed_background_start_event(self) -> None:
        release_start = threading.Event()
        controller = _BlockingRuntimeQueueControllerStub(release_start=release_start)
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())

        runtime.start_queue_in_background("workspace-1")
        self.assertTrue(
            controller.start_queue_started.wait(timeout=1.0),
            "background queue start did not begin",
        )
        release_start.set()
        self.assertTrue(
            _wait_until(lambda: not runtime._runtime_action_completion_queue.empty()),
            "background queue start completion was not queued",
        )

        runtime.close_session("session-1")
        runtime.process_background_events()
        events = runtime.drain_events()

        self.assertFalse(any(isinstance(event, QueueStartCompletedEvent) for event in events))
        self.assertEqual(["workspace-1"], controller.started_queue_ids)
        self.assertEqual(["session-1"], controller.closed_session_ids)
        self.assertEqual(["workspace-1"], controller.stopped_queue_ids)

    def test_stop_other_queue_does_not_suppress_background_start_completion(self) -> None:
        release_start = threading.Event()
        controller = _BlockingRuntimeQueueControllerStub(release_start=release_start)
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())

        runtime.start_queue_in_background("workspace-1")
        self.assertTrue(
            controller.start_queue_started.wait(timeout=1.0),
            "background queue start did not begin",
        )

        runtime.stop_queue("workspace-2")
        release_start.set()

        events: list[object] = []
        self.assertTrue(
            _wait_until(
                lambda: runtime.process_background_events() >= 0
                and not runtime.has_pending_background_work()
                and _drain_runtime_until_queue_start_completed(runtime, events)
            ),
            "other queue stop suppressed background queue start",
        )

        self.assertTrue(any(isinstance(event, QueueStartCompletedEvent) for event in events))
        self.assertEqual(["workspace-1"], controller.started_queue_ids)
        self.assertEqual(["workspace-2"], controller.stopped_queue_ids)

    def test_shutdown_waits_for_active_background_queue_start_cleanup(self) -> None:
        release_start = threading.Event()
        controller = _BlockingRuntimeQueueControllerStub(release_start=release_start)
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())

        runtime.start_queue_in_background("workspace-1")
        self.assertTrue(
            controller.start_queue_started.wait(timeout=1.0),
            "background queue start did not begin",
        )

        runtime.shutdown()

        self.assertTrue(runtime.has_pending_background_work())

        release_start.set()

        self.assertTrue(
            _wait_until(
                lambda: runtime.process_background_events() >= 0
                and not runtime.has_pending_background_work()
            ),
            "shutdown did not wait for queue start cleanup",
        )
        events = runtime.drain_events()

        self.assertFalse(any(isinstance(event, QueueStartCompletedEvent) for event in events))
        self.assertEqual(["workspace-1"], controller.started_queue_ids)
        self.assertEqual(["workspace-1"], controller.stopped_queue_ids)
        self.assertEqual(1, controller.stop_all_queue_calls)


class AppRuntimeSleepPreventionTests(unittest.TestCase):
    def test_start_and_stop_queue_toggle_sleep_prevention(self) -> None:
        controller = _RuntimeSleepControllerStub()
        preventer = _SleepPreventerStub()
        runtime = AppRuntime(
            controller=controller,
            repository=_RuntimeRepositoryStub(),
            system_sleep_preventer=preventer,
        )

        runtime.start_queue("workspace-1")
        runtime.stop_queue("workspace-1")

        self.assertEqual([True, False], preventer.active_values)

    def test_completion_poll_releases_sleep_prevention_when_queue_stops(self) -> None:
        controller = _RuntimeSleepControllerStub(stop_on_next_poll=True)
        preventer = _SleepPreventerStub()
        runtime = AppRuntime(
            controller=controller,
            repository=_RuntimeRepositoryStub(),
            system_sleep_preventer=preventer,
        )

        runtime.start_queue("workspace-1")
        runtime.process_background_events()

        self.assertEqual([True, False], preventer.active_values)

    def test_running_job_keeps_sleep_prevention_after_queue_stop(self) -> None:
        controller = _RuntimeSleepControllerStub(
            jobs=(
                _RuntimeJobStub(
                    job_id="job-1",
                    status=JobStatus.RUNNING,
                ),
            ),
        )
        preventer = _SleepPreventerStub()
        runtime = AppRuntime(
            controller=controller,
            repository=_RuntimeRepositoryStub(),
            system_sleep_preventer=preventer,
        )

        runtime.start_queue("workspace-1")
        runtime.stop_queue("workspace-1")

        self.assertEqual([True], preventer.active_values)

    def test_close_workspace_releases_sleep_prevention(self) -> None:
        controller = _RuntimeSleepControllerStub()
        preventer = _SleepPreventerStub()
        runtime = AppRuntime(
            controller=controller,
            repository=_RuntimeRepositoryStub(),
            system_sleep_preventer=preventer,
        )

        runtime.start_queue("workspace-1")
        runtime.close_workspace("workspace-1")

        self.assertEqual([True, False], preventer.active_values)


class AppRuntimeSettingsUpdateTests(unittest.TestCase):
    def test_update_settings_retries_waiting_jobs_and_saves_on_background_thread(self) -> None:
        controller = _RuntimeSettingsControllerStub(
            jobs=(
                _RuntimeJobStub(
                    job_id="job-1",
                    status=JobStatus.WAITING_FOR_CONFIGURATION,
                ),
            ),
        )
        repository = _RuntimePersistenceRepositoryStub()
        runtime = AppRuntime(controller=controller, repository=repository)
        updated_settings = AppSettings(
            output_font_size=15,
        )

        result = runtime.update_settings(updated_settings)

        self.assertIsNone(result.persistence_issue)
        self.assertEqual(updated_settings, runtime.settings)
        self.assertEqual((), result.retried_job_ids)
        events: list[object] = []
        self.assertTrue(
            _wait_until(lambda: _drain_runtime_until_retry_completed(runtime, events)),
            "background settings retry did not complete",
        )
        self.assertEqual(["job-1"], controller.retried_job_ids)
        self.assertTrue(
            any(
                isinstance(event, SettingsRetryCompletedEvent)
                and event.retried_job_ids == ("job-1",)
                for event in events
            )
        )
        self.assertGreaterEqual(controller.drain_ui_events_calls, 1)
        self.assertTrue(
            _wait_until(lambda: repository.saved_settings == [updated_settings]),
            "background settings save did not complete",
        )
        self.assertNotEqual(threading.get_ident(), repository.saved_settings_thread_ids[0])

    def test_update_settings_reports_background_save_failure_via_runtime_event(self) -> None:
        controller = _RuntimeSettingsControllerStub()
        repository = _RuntimePersistenceRepositoryStub(
            settings_save_error=PersistenceSaveError(
                "boom",
                path=Path("settings.json"),
                operation="save",
            )
        )
        runtime = AppRuntime(controller=controller, repository=repository)
        updated_settings = AppSettings(output_font_size=15)

        result = runtime.update_settings(updated_settings)

        self.assertIsNone(result.persistence_issue)
        self.assertEqual(updated_settings, runtime.settings)
        events: list[object] = []
        self.assertTrue(
            _wait_until(lambda: _drain_runtime_until_settings_save_failure(runtime, events)),
            "background settings failure was not surfaced",
        )
        self.assertTrue(any(isinstance(event, PersistenceIssueEvent) for event in events))
        self.assertTrue(
            any(
                isinstance(event, PersistenceIssueEvent)
                and event.issue.operation == "save_settings"
                for event in events
            )
        )

    def test_shutdown_waits_for_pending_settings_save_completion(self) -> None:
        controller = _RuntimeSettingsControllerStub()
        release_save = threading.Event()
        repository = _BlockingRuntimePersistenceRepositoryStub(
            release_settings_save=release_save
        )
        runtime = AppRuntime(controller=controller, repository=repository)
        updated_settings = AppSettings(output_font_size=15)

        runtime.update_settings(updated_settings)

        self.assertTrue(
            repository.settings_save_started.wait(timeout=1.0),
            "background settings save did not start",
        )

        runtime.shutdown()

        self.assertTrue(runtime.has_pending_background_work())

        release_save.set()

        self.assertTrue(
            _wait_until(lambda: repository.saved_settings == [updated_settings]),
            "background settings save did not finish",
        )
        self.assertTrue(runtime.has_pending_background_work())
        self.assertTrue(
            _wait_until(
                lambda: runtime.process_background_events() >= 0
                and not runtime.has_pending_background_work()
            )
        )

    def test_shutdown_preserves_settings_save_failure_until_runtime_event_is_processed(self) -> None:
        controller = _RuntimeSettingsControllerStub()
        release_save = threading.Event()
        repository = _BlockingRuntimePersistenceRepositoryStub(
            release_settings_save=release_save,
            settings_save_error=PersistenceSaveError(
                "boom",
                path=Path("settings.json"),
                operation="save",
            ),
        )
        runtime = AppRuntime(controller=controller, repository=repository)

        updated_settings = AppSettings(output_font_size=15)
        runtime.update_settings(updated_settings)

        self.assertTrue(
            repository.settings_save_started.wait(timeout=1.0),
            "background settings save did not start",
        )

        runtime.shutdown()
        release_save.set()

        self.assertTrue(
            _wait_until(lambda: repository.saved_settings == [updated_settings]),
            "background settings save did not finish",
        )
        self.assertTrue(runtime.has_pending_background_work())
        events: list[object] = []
        self.assertTrue(
            _wait_until(lambda: _drain_runtime_until_settings_save_failure(runtime, events)),
            "background settings failure was not surfaced during shutdown",
        )
        self.assertTrue(
            any(
                isinstance(event, PersistenceIssueEvent)
                and event.issue.operation == "save_settings"
                for event in events
            )
        )
        self.assertTrue(_wait_until(lambda: not runtime.has_pending_background_work()))

    def test_open_workspace_updates_saved_list_immediately_and_saves_on_background_thread(self) -> None:
        controller = _RuntimeWorkspaceControllerStub()
        repository = _RuntimePersistenceRepositoryStub()
        runtime = AppRuntime(controller=controller, repository=repository)

        with tempfile.TemporaryDirectory() as workspace_path:
            result = runtime.open_workspace(workspace_path)

            self.assertIsNone(result.persistence_issue)
            self.assertEqual(workspace_path, result.open_result.workspace_tab.workspace_path)
            self.assertEqual(
                (workspace_path,),
                tuple(item.path for item in runtime.list_saved_workspaces()),
            )
            self.assertTrue(
                _wait_until(lambda: len(repository.saved_workspaces) == 1),
                "background saved-workspace save did not complete",
            )
            self.assertEqual(
                (workspace_path,),
                tuple(item.path for item in repository.saved_workspaces[0]),
            )
            self.assertNotEqual(threading.get_ident(), repository.saved_workspaces_thread_ids[0])

    def test_workspace_open_completion_batch_saves_saved_workspaces_once(self) -> None:
        controller = _RuntimeWorkspaceControllerStub()
        repository = _RuntimePersistenceRepositoryStub()
        runtime = AppRuntime(controller=controller, repository=repository)
        completions = (
            WorkspaceOpenCompletedEvent(
                workspace_path=r"C:\Repo\Alpha",
                workspace_tab_id="workspace-1",
                created=True,
            ),
            WorkspaceOpenCompletedEvent(
                workspace_path=r"C:\Repo\Beta",
                workspace_tab_id="workspace-2",
                created=True,
            ),
            WorkspaceOpenCompletedEvent(
                workspace_path=r"C:\Repo\Gamma",
                workspace_tab_id="workspace-3",
                created=True,
            ),
        )
        for event in completions:
            runtime._runtime_action_completion_queue.put(
                _RuntimeActionCompletion(event=event)
            )

        with patch("app.runtime.utc_now", side_effect=(_dt(10), _dt(11), _dt(12))):
            runtime.process_background_events()

        expected_paths = (r"C:\Repo\Gamma", r"C:\Repo\Beta", r"C:\Repo\Alpha")
        self.assertEqual(
            expected_paths,
            tuple(item.path for item in runtime.list_saved_workspaces()),
        )
        self.assertEqual(completions, runtime.drain_events())
        self.assertTrue(
            _wait_until(lambda: len(repository.saved_workspaces) == 1),
            "batched saved-workspace save did not complete",
        )
        self.assertEqual(
            expected_paths,
            tuple(item.path for item in repository.saved_workspaces[0]),
        )

    def test_persistence_worker_coalesces_pending_save_requests_by_key(self) -> None:
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._persistence_request_queue = Queue()
        runtime._persistence_completion_queue = Queue()
        save_calls: list[str] = []

        def save_action(label: str):
            def save() -> _SaveResultStub:
                save_calls.append(label)
                return _SaveResultStub()

            return save

        runtime._enqueue_persistence_save(
            save_action("first"),
            coalesce_key="saved_workspaces",
        )
        runtime._enqueue_persistence_save(
            save_action("latest"),
            coalesce_key="saved_workspaces",
        )
        runtime._persistence_request_queue.put(None)

        worker = threading.Thread(target=runtime._run_persistence_worker)
        worker.start()
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(["latest"], save_calls)
        self.assertEqual(1, runtime._persistence_completion_queue.qsize())

    def test_open_workspace_in_background_keeps_controller_lock_free_during_validation(self) -> None:
        controller = _RuntimeWorkspaceControllerStub()
        repository = _RuntimePersistenceRepositoryStub()
        runtime = AppRuntime(controller=controller, repository=repository)
        validation_started = threading.Event()
        release_validation = threading.Event()

        def blocking_validate(workspace_path: str) -> None:
            del workspace_path
            validation_started.set()
            release_validation.wait(timeout=1.0)

        with tempfile.TemporaryDirectory() as workspace_path:
            with patch("app.runtime.validate_workspace_path", blocking_validate):
                runtime.open_workspace_in_background(workspace_path)
                self.assertTrue(
                    validation_started.wait(timeout=1.0),
                    "background workspace validation did not start",
                )

                controller_lock = runtime._get_controller_state_lock()
                acquired = controller_lock.acquire(blocking=False)
                if acquired:
                    controller_lock.release()
                self.assertTrue(acquired, "workspace validation held the controller lock")

                release_validation.set()
                events: list[object] = []
                self.assertTrue(
                    _wait_until(
                        lambda: _drain_runtime_until_workspace_open_completed(
                            runtime,
                            events,
                        )
                    ),
                    "background workspace open did not complete",
                )

            self.assertTrue(
                any(isinstance(event, WorkspaceOpenCompletedEvent) for event in events)
            )
            self.assertEqual(
                (workspace_path,),
                tuple(item.path for item in runtime.list_saved_workspaces()),
            )

    def test_delete_saved_workspace_updates_list_immediately_and_saves_on_background_thread(self) -> None:
        alpha = SavedWorkspace(
            path=r"C:\Repo\Alpha",
            display_name="Alpha",
            added_at=_dt(1),
            last_selected_at=_dt(3),
        )
        beta = SavedWorkspace(
            path=r"C:\Repo\Beta",
            display_name="Beta",
            added_at=_dt(2),
            last_selected_at=_dt(4),
        )
        controller = _RuntimeWorkspaceControllerStub()
        repository = _RuntimePersistenceRepositoryStub(
            initial_saved_workspaces=(alpha, beta),
        )
        runtime = AppRuntime(controller=controller, repository=repository)

        deleted_workspace = runtime.delete_saved_workspace(r"c:/repo/alpha/")

        self.assertEqual(alpha, deleted_workspace)
        self.assertEqual(
            (beta.path,),
            tuple(item.path for item in runtime.list_saved_workspaces()),
        )
        self.assertTrue(
            _wait_until(lambda: len(repository.saved_workspaces) == 1),
            "background saved-workspace delete did not persist",
        )
        self.assertEqual(
            (beta.path,),
            tuple(item.path for item in repository.saved_workspaces[0]),
        )
        self.assertNotEqual(threading.get_ident(), repository.saved_workspaces_thread_ids[0])


class _FakeExecutor:
    def __init__(self) -> None:
        self.blocked_prompts: set[str] = set()
        self.launched_requests: list[JobExecutionRequest] = []
        self.canceled_handles: list[ExecutionHandle] = []

    def validate(self, request: JobExecutionRequest) -> str | None:
        if request.prompt in self.blocked_prompts:
            return "설정 확인 필요"
        return None

    def launch(self, request: JobExecutionRequest) -> ExecutionHandle:
        self.launched_requests.append(request)
        return ExecutionHandle(handle_id=request.job_id)

    def cancel(self, handle: ExecutionHandle) -> None:
        self.canceled_handles.append(handle)


class _CountingJobDict(dict[str, Job]):
    def __init__(self, jobs: dict[str, Job]) -> None:
        super().__init__(jobs)
        self.values_calls = 0

    def values(self) -> ValuesView[Job]:
        self.values_calls += 1
        return super().values()


class _RuntimeControllerStub:
    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        return 0

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()

    def has_pending_background_work(self) -> bool:
        return False


class _RuntimeEventsControllerStub:
    def __init__(self, events: tuple[object, ...]) -> None:
        self._events = events

    def drain_ui_events(self) -> tuple[object, ...]:
        events = self._events
        self._events = ()
        return events


class _RuntimeDispatchControllerStub:
    def __init__(self, *, background_events_to_process: int = 0) -> None:
        self.session_manager = _RuntimeSubmitSessionManagerStub()
        self._background_events_to_process = background_events_to_process
        self._pending_dispatch = False
        self.submit_dispatch_immediately_values: list[bool] = []
        self.process_dispatch_immediately_values: list[bool] = []
        self.dispatch_thread_ids: list[int] = []
        self.block_dispatch = False
        self.dispatch_started = threading.Event()
        self.release_dispatch = threading.Event()

    def submit_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        dispatch_immediately: bool = True,
        execution_options: AgentExecutionOptions | None = None,
    ) -> _RuntimeJobStub:
        del session_tab_id, prompt, execution_options
        self.submit_dispatch_immediately_values.append(dispatch_immediately)
        self._pending_dispatch = True
        return _RuntimeJobStub(job_id="job-1", status=JobStatus.QUEUED)

    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        del max_items
        self.process_dispatch_immediately_values.append(dispatch_immediately)
        if self._background_events_to_process <= 0:
            return 0

        self._background_events_to_process -= 1
        self._pending_dispatch = True
        return 1

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()

    def has_pending_dispatch(self) -> bool:
        return self._pending_dispatch

    def dispatch_next_job(self, *, excluded_workspace_tab_ids=()) -> None:
        del excluded_workspace_tab_ids
        self.dispatch_thread_ids.append(threading.get_ident())
        self.dispatch_started.set()
        if self.block_dispatch:
            self.release_dispatch.wait(timeout=1.0)
        self._pending_dispatch = False

    def has_pending_background_work(self) -> bool:
        return False

    def stop_all_queues(self) -> None:
        return None


class _RuntimeSubmitSessionManagerStub:
    def lock_session_execution_options(
        self,
        session_tab_id: str,
        execution_options: AgentExecutionOptions,
    ) -> SessionTab:
        return SessionTab(
            session_tab_id=session_tab_id,
            workspace_tab_id="workspace-1",
            display_name="S1",
            execution_options=execution_options,
            execution_options_locked=True,
        )


@dataclass(slots=True, frozen=True)
class _RuntimeSessionOpenWorkspaceTabStub:
    workspace_path: str


class _RuntimeSessionOpenWorkspaceManagerStub:
    def __init__(self, workspace_paths: dict[str, str] | None = None) -> None:
        self._workspace_paths = workspace_paths or {}

    def get_workspace_tab(self, workspace_tab_id: str) -> _RuntimeSessionOpenWorkspaceTabStub:
        return _RuntimeSessionOpenWorkspaceTabStub(
            workspace_path=self._workspace_paths.get(workspace_tab_id, workspace_tab_id)
        )


class _RuntimeSessionOpenControllerStub:
    def __init__(self, workspace_paths: dict[str, str] | None = None) -> None:
        self.workspace_manager = _RuntimeSessionOpenWorkspaceManagerStub(
            workspace_paths
        )
        self.session_manager = self
        self.open_session_execution_options: list[AgentExecutionOptions | None] = []
        self.open_preset_session_execution_options: list[
            AgentExecutionOptions | None
        ] = []
        self._session_tabs: dict[str, SessionTab] = {}
        self._next_session_number = 1

    def open_session(
        self,
        workspace_tab_id: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ) -> SessionTab:
        self.open_session_execution_options.append(execution_options)
        session_tab = self._new_session_tab(
            workspace_tab_id,
            kind=SessionTabKind.NORMAL,
            execution_options=execution_options,
        )
        self._session_tabs[session_tab.session_tab_id] = session_tab
        return session_tab

    def open_preset_session(
        self,
        workspace_tab_id: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ) -> SessionTab:
        self.open_preset_session_execution_options.append(execution_options)
        session_tab = self._new_session_tab(
            workspace_tab_id,
            kind=SessionTabKind.PRESET,
            execution_options=execution_options,
        )
        self._session_tabs[session_tab.session_tab_id] = session_tab
        return session_tab

    def get_session_tab(self, session_tab_id: str) -> SessionTab:
        return self._session_tabs[session_tab_id]

    def set_session_execution_options(
        self,
        session_tab_id: str,
        execution_options: AgentExecutionOptions,
    ) -> SessionTab:
        session_tab = self._session_tabs[session_tab_id]
        updated_session_tab = replace(
            session_tab,
            execution_options=execution_options,
        )
        self._session_tabs[session_tab_id] = updated_session_tab
        return updated_session_tab

    def _new_session_tab(
        self,
        workspace_tab_id: str,
        *,
        kind: SessionTabKind,
        execution_options: AgentExecutionOptions | None,
    ) -> SessionTab:
        session_number = self._next_session_number
        self._next_session_number += 1
        return SessionTab(
            session_tab_id=f"session-{session_number}",
            workspace_tab_id=workspace_tab_id,
            display_name=f"S{session_number}",
            kind=kind,
            execution_options=execution_options or AgentExecutionOptions(),
        )


class _RuntimePromptImportWorkspaceManagerStub:
    def get_workspace_tab(self, workspace_tab_id: str) -> object:
        if workspace_tab_id != "workspace-1":
            raise KeyError(workspace_tab_id)
        return object()


class _RuntimePromptImportSessionManagerStub:
    def __init__(self) -> None:
        self.locked_execution_options: list[tuple[str, AgentExecutionOptions]] = []

    def lock_session_execution_options(
        self,
        session_tab_id: str,
        execution_options: AgentExecutionOptions,
    ) -> SessionTab:
        self.locked_execution_options.append((session_tab_id, execution_options))
        return SessionTab(
            session_tab_id=session_tab_id,
            workspace_tab_id="workspace-1",
            display_name=session_tab_id,
            execution_options=execution_options,
            execution_options_locked=True,
        )


class _RuntimePromptImportControllerStub:
    def __init__(self) -> None:
        self.workspace_manager = _RuntimePromptImportWorkspaceManagerStub()
        self.session_manager = _RuntimePromptImportSessionManagerStub()
        self.open_session_workspace_ids: list[str] = []
        self.open_session_execution_options: list[AgentExecutionOptions | None] = []
        self.submitted_jobs: list[tuple[str, str, bool]] = []
        self.submitted_execution_options: list[AgentExecutionOptions | None] = []
        self._next_session_number = 1
        self._next_job_number = 1

    def open_session(
        self,
        workspace_tab_id: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ) -> SessionTab:
        self.open_session_workspace_ids.append(workspace_tab_id)
        self.open_session_execution_options.append(execution_options)
        session_tab = SessionTab(
            session_tab_id=f"session-{self._next_session_number}",
            workspace_tab_id=workspace_tab_id,
            display_name=f"S{self._next_session_number}",
            execution_options=execution_options or AgentExecutionOptions(),
        )
        self._next_session_number += 1
        return session_tab

    def submit_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        dispatch_immediately: bool = True,
        execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        self.submitted_jobs.append((session_tab_id, prompt, dispatch_immediately))
        self.submitted_execution_options.append(execution_options)
        job = Job(
            job_id=f"job-{self._next_job_number}",
            workspace_tab_id="workspace-1",
            session_tab_id=session_tab_id,
            prompt=prompt,
            status=JobStatus.QUEUED,
        )
        self._next_job_number += 1
        return job

    def submit_jobs(
        self,
        job_requests: tuple[tuple[str, str], ...] | list[tuple[str, str]],
        *,
        dispatch_immediately: bool = True,
        execution_options: AgentExecutionOptions | None = None,
    ) -> tuple[Job, ...]:
        return tuple(
            self.submit_job(
                session_tab_id,
                prompt,
                dispatch_immediately=dispatch_immediately,
                execution_options=execution_options,
            )
            for session_tab_id, prompt in job_requests
        )

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()


class _BlockingRuntimeQueueControllerStub:
    def __init__(self, *, release_start: threading.Event) -> None:
        self.workspace_manager = _RuntimeQueueWorkspaceManagerStub()
        self.session_manager = _RuntimeQueueSessionManagerStub()
        self._release_start = release_start
        self.start_queue_started = threading.Event()
        self.started_queue_ids: list[str | None] = []
        self.stopped_queue_ids: list[str | None] = []
        self.closed_workspace_ids: list[str] = []
        self.closed_session_ids: list[str] = []
        self.stop_all_queue_calls = 0

    def start_queue(self, workspace_tab_id: str | None = None) -> WorkspaceQueueState:
        self.started_queue_ids.append(workspace_tab_id)
        self.start_queue_started.set()
        self._release_start.wait(timeout=1.0)
        return WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id or "workspace-1",
            status=QueueStatus.STARTED,
        )

    def stop_queue(self, workspace_tab_id: str | None = None) -> WorkspaceQueueState:
        self.stopped_queue_ids.append(workspace_tab_id)
        return WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id or "workspace-1",
            status=QueueStatus.STOPPED,
        )

    def stop_all_queues(self) -> None:
        self.stop_all_queue_calls += 1

    def close_workspace(self, workspace_tab_id: str) -> WorkspaceQueueState:
        self.closed_workspace_ids.append(workspace_tab_id)
        return self.stop_queue(workspace_tab_id)

    def close_session(self, session_tab_id: str) -> WorkspaceQueueState:
        self.closed_session_ids.append(session_tab_id)
        return self.stop_queue("workspace-1")

    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        return 0

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()

    def has_pending_background_work(self) -> bool:
        return False


class _RuntimeQueueWorkspaceManagerStub:
    def get_workspace_tab(self, workspace_tab_id: str) -> _RuntimeQueueWorkspaceTabStub:
        return _RuntimeQueueWorkspaceTabStub(workspace_tab_id=workspace_tab_id)


class _RuntimeQueueSessionManagerStub:
    def get_session_tab(self, session_tab_id: str) -> _RuntimeQueueSessionTabStub:
        return _RuntimeQueueSessionTabStub(
            session_tab_id=session_tab_id,
            workspace_tab_id="workspace-1",
        )


class _RuntimeSleepControllerStub:
    def __init__(
        self,
        *,
        jobs: tuple[_RuntimeJobStub, ...] = (),
        stop_on_next_poll: bool = False,
    ) -> None:
        self.scheduler = _RuntimeSleepSchedulerStub(jobs=jobs)
        self._stop_on_next_poll = stop_on_next_poll

    def start_queue(self, workspace_tab_id: str | None = None) -> WorkspaceQueueState:
        return self.scheduler.set_queue_state(
            workspace_tab_id or "workspace-1",
            QueueStatus.STARTED,
        )

    def stop_queue(self, workspace_tab_id: str | None = None) -> WorkspaceQueueState:
        return self.scheduler.set_queue_state(
            workspace_tab_id or "workspace-1",
            QueueStatus.STOPPED,
        )

    def stop_all_queues(self) -> None:
        self.scheduler.stop_all_queues()

    def close_workspace(self, workspace_tab_id: str) -> WorkspaceQueueState:
        return self.scheduler.set_queue_state(workspace_tab_id, QueueStatus.STOPPED)

    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        del max_items, dispatch_immediately
        if not self._stop_on_next_poll:
            return 0
        self._stop_on_next_poll = False
        self.scheduler.set_queue_state("workspace-1", QueueStatus.STOPPED)
        return 1

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()

    def has_pending_background_work(self) -> bool:
        return False


class _RuntimeSleepSchedulerStub:
    def __init__(self, *, jobs: tuple[_RuntimeJobStub, ...]) -> None:
        self._jobs = jobs
        self._queue_states: dict[str, WorkspaceQueueState] = {}

    def set_queue_state(
        self,
        workspace_tab_id: str,
        status: QueueStatus,
    ) -> WorkspaceQueueState:
        queue_state = WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id,
            status=status,
        )
        self._queue_states[workspace_tab_id] = queue_state
        return queue_state

    def list_queue_states(self) -> tuple[WorkspaceQueueState, ...]:
        return tuple(self._queue_states[key] for key in sorted(self._queue_states))

    def list_jobs(self) -> tuple[_RuntimeJobStub, ...]:
        return self._jobs

    def stop_all_queues(self) -> None:
        for workspace_tab_id in tuple(self._queue_states):
            self.set_queue_state(workspace_tab_id, QueueStatus.STOPPED)


class _SleepPreventerStub:
    def __init__(self) -> None:
        self.active_values: list[bool] = []

    def set_active(self, active: bool) -> None:
        if self.active_values and self.active_values[-1] == active:
            return
        self.active_values.append(active)

    def release(self) -> None:
        self.set_active(False)


@dataclass(slots=True, frozen=True)
class _RuntimeQueueWorkspaceTabStub:
    workspace_tab_id: str
    display_name: str = "W1"
    open_state: TabOpenState = TabOpenState.OPEN


@dataclass(slots=True, frozen=True)
class _RuntimeQueueSessionTabStub:
    session_tab_id: str
    workspace_tab_id: str


@dataclass
class _RuntimeJobStub:
    job_id: str
    status: JobStatus
    session_tab_id: str = "session-1"
    workspace_tab_id: str = "workspace-1"


class _RuntimeSchedulerStub:
    def __init__(self, jobs: tuple[_RuntimeJobStub, ...]) -> None:
        self._jobs = jobs

    def list_jobs(self) -> tuple[_RuntimeJobStub, ...]:
        return self._jobs


class _RuntimeWorkspaceJobControllerStub:
    def __init__(self, jobs: tuple[Job, ...]) -> None:
        self.workspace_manager = _RuntimeWorkspaceJobWorkspaceManagerStub()
        self.scheduler = _RuntimeWorkspaceJobSchedulerStub(jobs)


class _RuntimeWorkspaceJobWorkspaceManagerStub:
    def __init__(self) -> None:
        self.requested_workspace_tab_ids: list[str] = []

    def get_workspace_tab(self, workspace_tab_id: str) -> object:
        self.requested_workspace_tab_ids.append(workspace_tab_id)
        return object()


class _RuntimeWorkspaceJobSchedulerStub:
    def __init__(self, jobs: tuple[Job, ...]) -> None:
        self._jobs = jobs
        self.list_jobs_calls = 0
        self.list_workspace_jobs_requests: list[str] = []
        self.list_jobs_by_workspace_requests: list[tuple[str, ...]] = []
        self.summarize_workspace_jobs_requests: list[tuple[str, ...]] = []
        self.workspace_has_jobs_requests: list[str] = []
        self.workspace_has_runnable_jobs_requests: list[str] = []

    def list_jobs(self) -> tuple[Job, ...]:
        self.list_jobs_calls += 1
        return self._jobs

    def list_workspace_jobs(self, workspace_tab_id: str) -> tuple[Job, ...]:
        self.list_workspace_jobs_requests.append(workspace_tab_id)
        return tuple(job for job in self._jobs if job.workspace_tab_id == workspace_tab_id)

    def list_jobs_by_workspace(
        self,
        workspace_tab_ids: tuple[str, ...],
    ) -> dict[str, tuple[Job, ...]]:
        self.list_jobs_by_workspace_requests.append(workspace_tab_ids)
        return {
            workspace_tab_id: tuple(
                job for job in self._jobs if job.workspace_tab_id == workspace_tab_id
            )
            for workspace_tab_id in workspace_tab_ids
        }

    def summarize_workspace_jobs(
        self,
        workspace_tab_ids: tuple[str, ...],
    ) -> dict[str, WorkspaceJobSummary]:
        self.summarize_workspace_jobs_requests.append(workspace_tab_ids)
        return {
            workspace_tab_id: WorkspaceJobSummary(
                has_jobs=any(
                    job.workspace_tab_id == workspace_tab_id for job in self._jobs
                ),
                has_running_job=any(
                    job.workspace_tab_id == workspace_tab_id
                    and job.status == JobStatus.RUNNING
                    for job in self._jobs
                ),
            )
            for workspace_tab_id in workspace_tab_ids
        }

    def workspace_has_jobs(self, workspace_tab_id: str) -> bool:
        self.workspace_has_jobs_requests.append(workspace_tab_id)
        return any(job.workspace_tab_id == workspace_tab_id for job in self._jobs)

    def workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        self.workspace_has_runnable_jobs_requests.append(workspace_tab_id)
        return any(
            job.workspace_tab_id == workspace_tab_id and job.status == JobStatus.QUEUED
            for job in self._jobs
        )


class _RuntimeWorkspacePathRunningControllerStub:
    def __init__(self, workspace_manager: WorkspaceManager, jobs: tuple[Job, ...]) -> None:
        self.workspace_manager = workspace_manager
        self.scheduler = _RuntimeWorkspaceJobSchedulerStub(jobs)


class _RuntimeDeleteControllerStub:
    def __init__(self, deleted_job: Job) -> None:
        self._deleted_job = deleted_job
        self.deleted_job_ids: list[str] = []

    def delete_job(self, job_id: str) -> Job:
        self.deleted_job_ids.append(job_id)
        return self._deleted_job

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()


class _RuntimeSettingsControllerStub:
    def __init__(self, *, jobs: tuple[_RuntimeJobStub, ...] = ()) -> None:
        self.scheduler = _RuntimeSchedulerStub(jobs)
        self.session_manager = _RuntimeSessionManagerStub()
        self.workspace_manager = _RuntimeWorkspaceManagerStub()
        self.retried_job_ids: list[str] = []
        self.drain_ui_events_calls = 0

    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        return 0

    def drain_ui_events(self) -> tuple[object, ...]:
        self.drain_ui_events_calls += 1
        return ()

    def has_pending_background_work(self) -> bool:
        return False

    def stop_all_queues(self) -> None:
        return None

    def retry_waiting_job(self, job_id: str) -> None:
        self.retried_job_ids.append(job_id)


@dataclass(slots=True, frozen=True)
class _RuntimeOpenTabStub:
    open_state: TabOpenState = TabOpenState.OPEN


class _RuntimeSessionManagerStub:
    def get_session_tab(self, session_tab_id: str) -> _RuntimeOpenTabStub:
        return _RuntimeOpenTabStub()


class _RuntimeWorkspaceManagerStub:
    def get_workspace_tab(self, workspace_tab_id: str) -> _RuntimeOpenTabStub:
        return _RuntimeOpenTabStub()


class _RuntimeWorkspaceControllerStub:
    def __init__(self) -> None:
        self.workspace_manager = WorkspaceManager()

    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        return 0

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()

    def has_pending_background_work(self) -> bool:
        return False

    def open_workspace(self, workspace_path: str):
        return self.workspace_manager.open_workspace(workspace_path)


class _RuntimeRepositoryStub:
    def load_settings(self) -> AppSettings:
        return AppSettings()

    def save_settings(self, settings: AppSettings) -> None:
        return None

    def load_saved_workspaces(self) -> tuple[object, ...]:
        return ()

    def save_saved_workspaces(self, workspaces: tuple[object, ...]) -> None:
        return None


class _SaveResultStub:
    issue = None


class _RuntimeSettingsRepositoryStub:
    def __init__(
        self,
        *,
        initial_settings: AppSettings | None = None,
        save_error: Exception | None = None,
    ) -> None:
        self._initial_settings = initial_settings or AppSettings()
        self._save_error = save_error
        self.saved_settings: list[AppSettings] = []

    def load_settings(self) -> AppSettings:
        return self._initial_settings

    def save_settings(self, settings: AppSettings) -> None:
        self.saved_settings.append(settings)
        if self._save_error is not None:
            raise self._save_error

    def load_saved_workspaces(self) -> tuple[object, ...]:
        return ()

    def save_saved_workspaces(self, workspaces: tuple[object, ...]) -> None:
        return None


class _RuntimePersistenceRepositoryStub:
    def __init__(
        self,
        *,
        initial_settings: AppSettings | None = None,
        initial_saved_workspaces: tuple[object, ...] = (),
        settings_save_error: Exception | None = None,
        saved_workspaces_save_error: Exception | None = None,
    ) -> None:
        self._initial_settings = initial_settings or AppSettings()
        self._initial_saved_workspaces = initial_saved_workspaces
        self._settings_save_error = settings_save_error
        self._saved_workspaces_save_error = saved_workspaces_save_error
        self.saved_settings: list[AppSettings] = []
        self.saved_settings_thread_ids: list[int] = []
        self.saved_workspaces: list[tuple[object, ...]] = []
        self.saved_workspaces_thread_ids: list[int] = []

    def load_settings(self) -> AppSettings:
        return self._initial_settings

    def save_settings(self, settings: AppSettings) -> None:
        self.saved_settings.append(settings)
        self.saved_settings_thread_ids.append(threading.get_ident())
        if self._settings_save_error is not None:
            raise self._settings_save_error

    def load_saved_workspaces(self) -> tuple[object, ...]:
        return self._initial_saved_workspaces

    def save_saved_workspaces(self, workspaces: tuple[object, ...]) -> None:
        self.saved_workspaces.append(workspaces)
        self.saved_workspaces_thread_ids.append(threading.get_ident())
        if self._saved_workspaces_save_error is not None:
            raise self._saved_workspaces_save_error


class _BlockingRuntimePersistenceRepositoryStub(_RuntimePersistenceRepositoryStub):
    def __init__(self, *, release_settings_save: threading.Event, **kwargs) -> None:
        super().__init__(**kwargs)
        self.settings_save_started = threading.Event()
        self._release_settings_save = release_settings_save

    def save_settings(self, settings: AppSettings) -> None:
        self.settings_save_started.set()
        self._release_settings_save.wait(timeout=1.0)
        super().save_settings(settings)


def _wait_until(predicate, *, timeout: float = 1.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _drain_runtime_until_retry_completed(
    runtime: AppRuntime,
    events: list[object],
) -> bool:
    runtime.process_background_events()
    events.extend(runtime.drain_events())
    return any(isinstance(event, SettingsRetryCompletedEvent) for event in events)


def _drain_runtime_until_queue_start_completed(
    runtime: AppRuntime,
    events: list[object],
) -> bool:
    runtime.process_background_events()
    events.extend(runtime.drain_events())
    return any(isinstance(event, QueueStartCompletedEvent) for event in events)


def _drain_runtime_until_settings_save_failure(
    runtime: AppRuntime,
    events: list[object],
) -> bool:
    runtime.process_background_events()
    events.extend(runtime.drain_events())
    return any(
        isinstance(event, PersistenceIssueEvent)
        and event.issue.operation == "save_settings"
        for event in events
    )


def _drain_runtime_until_workspace_open_completed(
    runtime: AppRuntime,
    events: list[object],
) -> bool:
    runtime.process_background_events()
    events.extend(runtime.drain_events())
    return any(isinstance(event, WorkspaceOpenCompletedEvent) for event in events)


if __name__ == "__main__":
    unittest.main()

