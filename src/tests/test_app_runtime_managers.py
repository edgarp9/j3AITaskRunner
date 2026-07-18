from __future__ import annotations

from tests._app_runtime_helpers import *
from domain import SessionExitHookConfig

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

    def test_session_exit_hook_config_is_runtime_session_state(self) -> None:
        session = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        config = SessionExitHookConfig(
            enabled=True,
            executable_path=r"C:\Tools\hook.exe",
            arguments=("--done",),
        )

        updated = self.session_manager.set_session_exit_hook_config(
            session.session_tab_id,
            config,
            when=_dt(2),
        )

        self.assertEqual(config, updated.exit_hook)
        self.assertEqual(
            config,
            self.session_manager.get_session_tab(session.session_tab_id).exit_hook,
        )
        self.assertEqual(_dt(2), updated.updated_at)

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


