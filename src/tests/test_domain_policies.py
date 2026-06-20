from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from domain.models import (
    AppSettings,
    DEFAULT_AGENT_PROVIDER,
    ExecutionMetadata,
    Job,
    JobStatus,
    SessionTab,
    SessionTabKind,
    SUPPORTED_AGENT_PROVIDERS,
    TabNameState,
    TabOpenState,
    normalize_agent_provider,
)
from domain.policies import (
    is_duplicate_workspace_path,
    is_execution_control_limit_enabled,
    is_valid_job_status_transition,
    issue_preset_candidate_session_tab_name,
    issue_session_tab_name,
    order_pending_jobs_by_queue_order,
    select_next_runnable_job,
    workspace_folder_display_name,
)


def _dt(minutes: int) -> datetime:
    base = datetime(2026, 4, 22, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes)


class WorkspacePathPolicyTests(unittest.TestCase):
    def test_duplicate_workspace_path_uses_normalized_path(self) -> None:
        self.assertTrue(
            is_duplicate_workspace_path(
                existing_paths=[r"C:\Work\Repo", r"D:\Other"],
                candidate_path=r"c:/work/repo\\",
            )
        )

        self.assertFalse(
            is_duplicate_workspace_path(
                existing_paths=["/workspace/repo", "/workspace/other"],
                candidate_path="/workspace/new",
            )
        )

    def test_workspace_folder_display_name_uses_final_folder_name(self) -> None:
        self.assertEqual("ccc", workspace_folder_display_name(r"C:\aaa\ccc"))
        self.assertEqual("ccc", workspace_folder_display_name(r"C:\aaa\ccc\\"))
        self.assertEqual("ccc", workspace_folder_display_name("/aaa/ccc/"))

    def test_workspace_folder_display_name_falls_back_to_normalized_path(self) -> None:
        self.assertEqual("C:\\", workspace_folder_display_name(r"C:\\"))
        self.assertEqual("/", workspace_folder_display_name("/"))


class TabNamingPolicyTests(unittest.TestCase):
    def test_session_name_increments_per_workspace_and_resets_when_closed(self) -> None:
        state = TabNameState()

        name1, state = issue_session_tab_name(state, workspace_tab_id="w-1", session_tabs=[])
        open_sessions = [
            SessionTab(
                session_tab_id="s-1",
                workspace_tab_id="w-1",
                display_name=name1,
            )
        ]
        name2, state = issue_session_tab_name(
            state,
            workspace_tab_id="w-1",
            session_tabs=open_sessions,
        )

        closed_sessions = [
            SessionTab(
                session_tab_id="s-1",
                workspace_tab_id="w-1",
                display_name="S1",
                open_state=TabOpenState.CLOSED,
            )
        ]
        name3, state = issue_session_tab_name(
            state,
            workspace_tab_id="w-1",
            session_tabs=closed_sessions,
        )

        self.assertEqual("S1", name1)
        self.assertEqual("S2", name2)
        self.assertEqual("S1", name3)
        self.assertEqual(2, state.next_session_numbers["w-1"])

    def test_normal_and_preset_names_share_workspace_counter(self) -> None:
        state = TabNameState()

        normal_name, state = issue_session_tab_name(
            state,
            workspace_tab_id="w-1",
            session_tabs=[],
            kind=SessionTabKind.NORMAL,
        )
        preset_name, state = issue_session_tab_name(
            state,
            workspace_tab_id="w-1",
            session_tabs=[
                SessionTab(
                    session_tab_id="s-1",
                    workspace_tab_id="w-1",
                    display_name=normal_name,
                    kind=SessionTabKind.NORMAL,
                )
            ],
            kind=SessionTabKind.PRESET,
        )
        next_normal_name, state = issue_session_tab_name(
            state,
            workspace_tab_id="w-1",
            session_tabs=[
                SessionTab(
                    session_tab_id="s-1",
                    workspace_tab_id="w-1",
                    display_name=normal_name,
                    kind=SessionTabKind.NORMAL,
                ),
                SessionTab(
                    session_tab_id="s-2",
                    workspace_tab_id="w-1",
                    display_name=preset_name,
                    kind=SessionTabKind.PRESET,
                ),
            ],
            kind=SessionTabKind.NORMAL,
        )

        self.assertEqual("S1", normal_name)
        self.assertEqual("P2", preset_name)
        self.assertEqual("S3", next_normal_name)
        self.assertEqual(4, state.next_session_numbers["w-1"])

    def test_s1_s2_then_preset_is_p3_and_next_normal_is_s4(self) -> None:
        state = TabNameState()

        first_normal_name, state = issue_session_tab_name(
            state,
            workspace_tab_id="w-1",
            session_tabs=[],
            kind=SessionTabKind.NORMAL,
        )
        second_normal_name, state = issue_session_tab_name(
            state,
            workspace_tab_id="w-1",
            session_tabs=[
                SessionTab(
                    session_tab_id="s-1",
                    workspace_tab_id="w-1",
                    display_name=first_normal_name,
                    kind=SessionTabKind.NORMAL,
                )
            ],
            kind=SessionTabKind.NORMAL,
        )
        preset_name, state = issue_session_tab_name(
            state,
            workspace_tab_id="w-1",
            session_tabs=[
                SessionTab(
                    session_tab_id="s-1",
                    workspace_tab_id="w-1",
                    display_name=first_normal_name,
                    kind=SessionTabKind.NORMAL,
                ),
                SessionTab(
                    session_tab_id="s-2",
                    workspace_tab_id="w-1",
                    display_name=second_normal_name,
                    kind=SessionTabKind.NORMAL,
                ),
            ],
            kind=SessionTabKind.PRESET,
        )
        next_normal_name, state = issue_session_tab_name(
            state,
            workspace_tab_id="w-1",
            session_tabs=[
                SessionTab(
                    session_tab_id="s-1",
                    workspace_tab_id="w-1",
                    display_name=first_normal_name,
                    kind=SessionTabKind.NORMAL,
                ),
                SessionTab(
                    session_tab_id="s-2",
                    workspace_tab_id="w-1",
                    display_name=second_normal_name,
                    kind=SessionTabKind.NORMAL,
                ),
                SessionTab(
                    session_tab_id="s-3",
                    workspace_tab_id="w-1",
                    display_name=preset_name,
                    kind=SessionTabKind.PRESET,
                ),
            ],
            kind=SessionTabKind.NORMAL,
        )

        self.assertEqual("S1", first_normal_name)
        self.assertEqual("S2", second_normal_name)
        self.assertEqual("P3", preset_name)
        self.assertEqual("S4", next_normal_name)
        self.assertEqual(5, state.next_session_numbers["w-1"])

    def test_preset_candidate_name_uses_parent_name_and_candidate_index(self) -> None:
        parent = SessionTab(
            session_tab_id="s-2",
            workspace_tab_id="w-1",
            display_name="P2",
            kind=SessionTabKind.PRESET,
        )
        existing_candidate = SessionTab(
            session_tab_id="s-3",
            workspace_tab_id="w-1",
            display_name="P2-1",
            kind=SessionTabKind.PRESET_CANDIDATE,
            parent_session_tab_id=parent.session_tab_id,
            candidate_index=1,
        )

        name, candidate_index = issue_preset_candidate_session_tab_name(
            parent,
            [parent, existing_candidate],
        )

        self.assertEqual("P2-2", name)
        self.assertEqual(2, candidate_index)


class JobStatusPolicyTests(unittest.TestCase):
    def test_valid_status_transitions_follow_documented_rules(self) -> None:
        valid_transitions = [
            (JobStatus.QUEUED, JobStatus.QUEUED),
            (JobStatus.QUEUED, JobStatus.WAITING_FOR_CONFIGURATION),
            (JobStatus.QUEUED, JobStatus.RUNNING),
            (JobStatus.WAITING_FOR_CONFIGURATION, JobStatus.QUEUED),
            (JobStatus.WAITING_FOR_CONFIGURATION, JobStatus.RUNNING),
            (JobStatus.RUNNING, JobStatus.COMPLETED),
            (JobStatus.RUNNING, JobStatus.FAILED),
            (JobStatus.RUNNING, JobStatus.CANCELED),
        ]

        for current_status, next_status in valid_transitions:
            with self.subTest(current_status=current_status, next_status=next_status):
                self.assertTrue(is_valid_job_status_transition(current_status, next_status))

    def test_invalid_status_transitions_are_rejected(self) -> None:
        invalid_transitions = [
            (JobStatus.WAITING_FOR_CONFIGURATION, JobStatus.COMPLETED),
            (JobStatus.RUNNING, JobStatus.QUEUED),
            (JobStatus.COMPLETED, JobStatus.RUNNING),
            (JobStatus.FAILED, JobStatus.CANCELED),
        ]

        for current_status, next_status in invalid_transitions:
            with self.subTest(current_status=current_status, next_status=next_status):
                self.assertFalse(is_valid_job_status_transition(current_status, next_status))


class ExecutionControlPolicyTests(unittest.TestCase):
    def test_positive_limit_values_are_enabled_and_zero_or_negative_are_disabled(self) -> None:
        self.assertTrue(is_execution_control_limit_enabled(1))
        self.assertTrue(is_execution_control_limit_enabled(120))
        self.assertFalse(is_execution_control_limit_enabled(0))
        self.assertFalse(is_execution_control_limit_enabled(-1))

    def test_app_settings_accepts_zero_execution_control_values(self) -> None:
        settings = AppSettings(
            execution_timeout_minutes=0,
            inactivity_timeout_minutes=0,
            termination_grace_seconds=0,
        )

        self.assertEqual(0, settings.execution_timeout_minutes)
        self.assertEqual(0, settings.inactivity_timeout_minutes)
        self.assertEqual(0, settings.termination_grace_seconds)

    def test_app_settings_rejects_invalid_execution_control_values(self) -> None:
        invalid_cases = (
            ("execution_timeout_minutes", -1),
            ("inactivity_timeout_minutes", "abc"),
            ("termination_grace_seconds", True),
        )

        for field_name, invalid_value in invalid_cases:
            with self.subTest(field_name=field_name, invalid_value=invalid_value):
                with self.assertRaisesRegex(ValueError, field_name):
                    AppSettings(**{field_name: invalid_value})


class AgentProviderPolicyTests(unittest.TestCase):
    def test_supported_agent_providers_are_preserved(self) -> None:
        for provider in SUPPORTED_AGENT_PROVIDERS:
            with self.subTest(provider=provider):
                self.assertEqual(provider, AppSettings(agent_provider=provider).agent_provider)

    def test_agent_provider_aliases_and_unknown_values_are_normalized(self) -> None:
        self.assertEqual("claude_code", normalize_agent_provider("Claude Code"))
        self.assertEqual("kilo_code", normalize_agent_provider("kilo-code"))
        self.assertEqual("opencode", normalize_agent_provider("open_code"))
        self.assertEqual("pi", normalize_agent_provider("pi.dev"))
        self.assertEqual("pi", normalize_agent_provider("Pi Coding Agent"))
        self.assertEqual(DEFAULT_AGENT_PROVIDER, normalize_agent_provider("unknown"))
        self.assertEqual(DEFAULT_AGENT_PROVIDER, AppSettings(agent_provider=None).agent_provider)

    def test_app_settings_keeps_executable_paths_per_provider(self) -> None:
        settings = AppSettings(
            agent_provider="opencode",
            executable_paths={
                "codex": r"C:\Tools\codex.exe",
                "opencode": r"C:\Tools\opencode.exe",
            },
        )

        self.assertEqual(r"C:\Tools\opencode.exe", settings.executable_path)
        self.assertEqual(r"C:\Tools\codex.exe", settings.executable_paths["codex"])
        self.assertEqual(r"C:\Tools\opencode.exe", settings.executable_paths["opencode"])

    def test_app_settings_current_executable_path_updates_provider_map(self) -> None:
        settings = AppSettings(
            agent_provider="codex",
            executable_path=r"C:\Tools\codex-new.exe",
            executable_paths={"codex": r"C:\Tools\codex-old.exe"},
        )

        self.assertEqual(r"C:\Tools\codex-new.exe", settings.executable_path)
        self.assertEqual(r"C:\Tools\codex-new.exe", settings.executable_paths["codex"])

    def test_execution_metadata_keeps_general_version_and_codex_alias(self) -> None:
        codex_metadata = ExecutionMetadata(codex_cli_version="codex-cli 1.0")
        general_metadata = ExecutionMetadata(
            agent_provider="claude_code",
            agent_version="claude 2.0",
        )

        self.assertEqual(DEFAULT_AGENT_PROVIDER, codex_metadata.agent_provider)
        self.assertEqual("codex-cli 1.0", codex_metadata.agent_version)
        self.assertEqual("codex-cli 1.0", codex_metadata.codex_cli_version)
        self.assertEqual("claude_code", general_metadata.agent_provider)
        self.assertEqual("claude 2.0", general_metadata.agent_version)
        self.assertIsNone(general_metadata.codex_cli_version)


class SchedulingPolicyTests(unittest.TestCase):
    def test_oldest_queued_job_is_selected_even_with_same_session_follow_up(self) -> None:
        previous_job = Job(
            job_id="job-0",
            workspace_tab_id="w-1",
            session_tab_id="s-1",
            prompt="done",
            status=JobStatus.COMPLETED,
            queue_order=0,
            created_at=_dt(0),
        )
        jobs = [
            Job(
                job_id="job-1",
                workspace_tab_id="w-2",
                session_tab_id="s-2",
                prompt="older other session",
                status=JobStatus.QUEUED,
                queue_order=1,
                created_at=_dt(1),
            ),
            Job(
                job_id="job-2",
                workspace_tab_id="w-1",
                session_tab_id="s-1",
                prompt="same session follow-up",
                status=JobStatus.QUEUED,
                queue_order=2,
                created_at=_dt(2),
            ),
        ]

        selected = select_next_runnable_job(jobs, previous_job=previous_job)

        self.assertIsNotNone(selected)
        self.assertEqual("job-1", selected.job_id)

    def test_oldest_queued_job_is_selected_when_same_session_has_no_follow_up(self) -> None:
        previous_job = Job(
            job_id="job-0",
            workspace_tab_id="w-1",
            session_tab_id="s-1",
            prompt="done",
            status=JobStatus.COMPLETED,
            created_at=_dt(0),
        )
        jobs = [
            Job(
                job_id="job-1",
                workspace_tab_id="w-2",
                session_tab_id="s-2",
                prompt="first queued",
                status=JobStatus.QUEUED,
                queue_order=1,
                created_at=_dt(1),
            ),
            Job(
                job_id="job-2",
                workspace_tab_id="w-3",
                session_tab_id="s-3",
                prompt="second queued",
                status=JobStatus.QUEUED,
                queue_order=2,
                created_at=_dt(2),
            ),
        ]

        selected = select_next_runnable_job(jobs, previous_job=previous_job)

        self.assertIsNotNone(selected)
        self.assertEqual("job-1", selected.job_id)

    def test_waiting_for_configuration_jobs_are_excluded_from_scheduler(self) -> None:
        jobs = [
            Job(
                job_id="job-1",
                workspace_tab_id="w-1",
                session_tab_id="s-1",
                prompt="needs config",
                status=JobStatus.WAITING_FOR_CONFIGURATION,
                queue_order=1,
                created_at=_dt(1),
            ),
            Job(
                job_id="job-2",
                workspace_tab_id="w-2",
                session_tab_id="s-2",
                prompt="ready",
                status=JobStatus.QUEUED,
                queue_order=2,
                created_at=_dt(2),
            ),
        ]

        selected = select_next_runnable_job(jobs)

        self.assertIsNotNone(selected)
        self.assertEqual("job-2", selected.job_id)

    def test_pending_jobs_keep_queue_order(self) -> None:
        jobs = [
            Job(
                job_id="job-1",
                workspace_tab_id="w-1",
                session_tab_id="s-1",
                prompt="session one first",
                status=JobStatus.QUEUED,
                queue_order=1,
                created_at=_dt(1),
            ),
            Job(
                job_id="job-2",
                workspace_tab_id="w-1",
                session_tab_id="s-2",
                prompt="session two first",
                status=JobStatus.QUEUED,
                queue_order=2,
                created_at=_dt(2),
            ),
            Job(
                job_id="job-3",
                workspace_tab_id="w-1",
                session_tab_id="s-2",
                prompt="session two second",
                status=JobStatus.QUEUED,
                queue_order=3,
                created_at=_dt(3),
            ),
            Job(
                job_id="job-4",
                workspace_tab_id="w-1",
                session_tab_id="s-1",
                prompt="session one second",
                status=JobStatus.QUEUED,
                queue_order=4,
                created_at=_dt(4),
            ),
        ]

        ordered = order_pending_jobs_by_queue_order(jobs)

        self.assertEqual(
            ("job-1", "job-2", "job-3", "job-4"),
            tuple(job.job_id for job in ordered),
        )

    def test_pending_job_order_ignores_session_display_priority(self) -> None:
        jobs = [
            Job(
                job_id="job-1",
                workspace_tab_id="w-1",
                session_tab_id="s-2",
                prompt="older other session",
                status=JobStatus.QUEUED,
                queue_order=1,
                created_at=_dt(1),
            ),
            Job(
                job_id="job-2",
                workspace_tab_id="w-1",
                session_tab_id="s-1",
                prompt="preferred session",
                status=JobStatus.QUEUED,
                queue_order=2,
                created_at=_dt(2),
            ),
        ]

        ordered = order_pending_jobs_by_queue_order(jobs)

        self.assertEqual(("job-1", "job-2"), tuple(job.job_id for job in ordered))


if __name__ == "__main__":
    unittest.main()
