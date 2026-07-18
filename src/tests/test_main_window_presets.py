from __future__ import annotations

from tests._main_window_helpers import *
from app.runtime import PresetManualCandidateSelectionRequiredEvent
from domain import PresetCandidate


class MainWindowPresetSessionTests(unittest.TestCase):
    def test_workspace_button_row_places_preset_add_after_new_session(self) -> None:
        self.assertEqual(
            ("New Session", "New Preset", "Import"),
            tuple(spec.text for spec in WORKSPACE_SESSION_ACTION_BUTTONS),
        )
        self.assertEqual(
            (2, 3, 4),
            tuple(spec.column for spec in WORKSPACE_SESSION_ACTION_BUTTONS),
        )

    def test_create_preset_session_opens_view_and_selects_tab(self) -> None:
        runtime = _CreatePresetSessionRuntimeStub()
        window = _CreatePresetSessionWindowStub(runtime)

        MainWindow._create_preset_session_for_workspace(window, "workspace-1")

        self.assertEqual(["workspace-1"], runtime.open_preset_session_workspace_ids)
        self.assertEqual(["session-preset-1"], window.ensured_session_ids)
        self.assertEqual(["session-preset-1"], window.refreshed_session_ids)
        self.assertEqual(["workspace-1"], window.selected_workspace_ids)
        self.assertEqual([("workspace-1", "session-preset-1")], window.selected_session_ids)
        self.assertEqual(["P1 프리셋 생성"], window.status_messages)

    def test_bulk_import_registers_one_session_per_text_block(self) -> None:
        runtime = _BulkImportRuntimeStub()
        window = _BulkImportWindowStub(runtime)
        raw_text = """ignored
```text
/goal one
```
outside
```text
/goal two
```
"""

        with patch("ui.main_window.BulkPromptImportDialog") as dialog_cls:
            dialog_cls.return_value.show_modal.return_value = (
                BulkPromptImportDialogResult(
                    raw_text=raw_text,
                    auto_commit_enabled=False,
                    step_execution_mode=StepExecutionMode.PER_STEP_SESSION,
                )
            )

            MainWindow._open_bulk_import_dialog_for_workspace(window, "workspace-1")

        dialog_cls.assert_called_once_with(
            window,
            initial_auto_commit=True,
            ui_language="ko",
        )
        self.assertEqual(
            [
                (
                    "workspace-1",
                    ("/goal one", "/goal two"),
                    False,
                    StepExecutionMode.PER_STEP_SESSION,
                )
            ],
            runtime.import_calls,
        )
        self.assertEqual(["session-1", "session-2"], window.ensured_session_ids)
        self.assertEqual(
            [("session-1", False), ("session-2", False)],
            window.auto_commit_states,
        )
        self.assertEqual(1, window.drain_runtime_events_calls)
        self.assertEqual(
            [("session-1", "job-1"), ("session-2", "job-2")],
            window.refreshed_session_ids,
        )
        self.assertEqual(["workspace-1"], window.selected_workspace_ids)
        self.assertEqual([("workspace-1", "session-1")], window.selected_session_ids)
        self.assertEqual([("workspace-1", "job-1")], window.refreshed_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            ["세션 2개, 작업 2건 등록"],
            window.status_messages,
        )

    def test_bulk_import_registers_steps_in_single_session_by_default(self) -> None:
        runtime = _BulkImportRuntimeStub()
        window = _BulkImportWindowStub(runtime)
        raw_text = """ignored
```text
/goal one
```
outside
```text
/goal two
```
"""

        with patch("ui.main_window.BulkPromptImportDialog") as dialog_cls:
            dialog_cls.return_value.show_modal.return_value = (
                BulkPromptImportDialogResult(
                    raw_text=raw_text,
                    auto_commit_enabled=True,
                )
            )

            MainWindow._open_bulk_import_dialog_for_workspace(window, "workspace-1")

        self.assertEqual(
            [
                (
                    "workspace-1",
                    ("/goal one", "/goal two"),
                    True,
                    StepExecutionMode.SINGLE_SESSION,
                )
            ],
            runtime.import_calls,
        )
        self.assertEqual(["session-1"], window.ensured_session_ids)
        self.assertEqual([("session-1", True)], window.auto_commit_states)
        self.assertEqual(1, window.drain_runtime_events_calls)
        self.assertEqual([("session-1", "job-1")], window.refreshed_session_ids)
        self.assertEqual(["workspace-1"], window.selected_workspace_ids)
        self.assertEqual([("workspace-1", "session-1")], window.selected_session_ids)
        self.assertEqual([("workspace-1", "job-1")], window.refreshed_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            ["세션 1개, 작업 4건 등록"],
            window.status_messages,
        )

    def test_bulk_import_reports_text_block_parse_error(self) -> None:
        runtime = _BulkImportRuntimeStub()
        window = _BulkImportWindowStub(runtime)

        with (
            patch("ui.main_window.BulkPromptImportDialog") as dialog_cls,
            patch("ui.main_window.messagebox.showerror") as showerror,
        ):
            dialog_cls.return_value.show_modal.return_value = (
                BulkPromptImportDialogResult(
                    raw_text="/goal without fence",
                    auto_commit_enabled=True,
                )
            )

            MainWindow._open_bulk_import_dialog_for_workspace(window, "workspace-1")

        self.assertEqual([], runtime.import_calls)
        showerror.assert_called_once_with(
            "가져오기 오류",
            "가져올 ```text 코드 블록을 입력하세요.",
            parent=window,
        )

    def test_bulk_import_button_smoke_creates_sessions_and_jobs_with_runtime(self) -> None:
        with TemporaryDirectory() as storage_dir, TemporaryDirectory() as workspace_dir:
            storage_root = Path(storage_dir)
            runtime = build_runtime(storage_root=storage_root)
            workspace_result = runtime.open_workspace(workspace_dir)
            workspace_tab = workspace_result.open_result.workspace_tab

            window: MainWindow | None = None
            try:
                try:
                    window = MainWindow(runtime)
                except tk.TclError as error:
                    if _is_tk_display_unavailable(error):
                        self.skipTest(f"Tk display is unavailable: {error}")
                    raise

                window.withdraw()
                window.update_idletasks()
                workspace_view = window._workspace_views[workspace_tab.workspace_tab_id]
                import_buttons = _find_widgets_by_text(workspace_view.frame, "Import")
                self.assertEqual(1, len(import_buttons))

                raw_text = """memo
```text
/goal imported one
```
```text
/goal imported two
```
"""
                with patch("ui.main_window.BulkPromptImportDialog") as dialog_cls:
                    dialog_cls.return_value.show_modal.return_value = (
                        BulkPromptImportDialogResult(
                            raw_text=raw_text,
                            auto_commit_enabled=True,
                        )
                    )
                    import_buttons[0].invoke()

                window.update_idletasks()

                sessions = runtime.list_session_tabs(
                    workspace_tab.workspace_tab_id,
                    include_closed=False,
                )
                self.assertEqual(("S1",), tuple(tab.display_name for tab in sessions))
                self.assertEqual(
                    (SessionTabKind.NORMAL,),
                    tuple(tab.kind for tab in sessions),
                )
                self.assertEqual(
                    ("session-tab-1",),
                    tuple(workspace_view.session_views),
                )

                first_jobs = runtime.list_jobs(session_tab_id=sessions[0].session_tab_id)
                self.assertEqual(
                    (
                        "/goal imported one",
                        AUTO_COMMIT_PROMPT,
                        "/goal imported two",
                        AUTO_COMMIT_PROMPT,
                    ),
                    tuple(job.prompt for job in first_jobs),
                )
                self.assertFalse(
                    workspace_view.session_views[sessions[0].session_tab_id].prompt_text
                    is None
                )
                self.assertIn(
                    "Registered 1 sessions and 4 jobs",
                    window._status_message_var.get(),
                )
            finally:
                if window is not None:
                    _close_tk_window(window)
                else:
                    _shutdown_runtime(runtime)

    def test_preset_manual_candidates_tab_renders_and_continue_submits_ids(self) -> None:
        with TemporaryDirectory() as storage_dir, TemporaryDirectory() as workspace_dir:
            storage_root = Path(storage_dir)
            _write_prompt_pair(storage_root, language="Python", instruction="bug")
            runtime = build_runtime(storage_root=storage_root)
            workspace_result = runtime.open_workspace(workspace_dir)
            workspace_tab = workspace_result.open_result.workspace_tab
            parent = runtime.open_preset_session(workspace_tab.workspace_tab_id)

            window: MainWindow | None = None
            try:
                try:
                    window = MainWindow(runtime)
                except tk.TclError as error:
                    if _is_tk_display_unavailable(error):
                        self.skipTest(f"Tk display is unavailable: {error}")
                    raise

                window.withdraw()
                window.update_idletasks()
                workspace_view = window._workspace_views[workspace_tab.workspace_tab_id]
                preset_widgets = workspace_view.session_views[parent.session_tab_id]
                continued_calls: list[tuple[str, tuple[str, ...]]] = []
                runtime.continue_preset_manual_selection_in_background = (
                    lambda session_tab_id, selected_ids: continued_calls.append(
                        (session_tab_id, tuple(selected_ids))
                    )
                )
                updates = RuntimeUiUpdateBatch()
                event = PresetManualCandidateSelectionRequiredEvent(
                    workspace_tab_id=workspace_tab.workspace_tab_id,
                    parent_session_tab_id=parent.session_tab_id,
                    candidates=(
                        PresetCandidate(
                            id="c1",
                            title="Fix parser",
                            problem="problem one",
                            evidence=("app/a.py:1", "tests/test_a.py:2"),
                            priority="high",
                            risk="low",
                            impact="impact one",
                        ),
                        PresetCandidate(
                            id="c2",
                            title="Fix renderer",
                            problem="problem two",
                            evidence="ui/view.py:3",
                            priority="medium",
                            risk="medium",
                            impact="impact two",
                        ),
                    ),
                )

                MainWindow._apply_runtime_event(window, event, updates)
                MainWindow._apply_runtime_ui_updates(window, updates)
                window.update_idletasks()

                self.assertEqual(
                    str(preset_widgets.candidates_tab_frame),
                    preset_widgets.body_notebook.select(),
                )
                self.assertEqual(
                    "disabled",
                    str(preset_widgets.preset_candidates_continue_button.cget("state")),
                )
                candidate_text = "\n".join(
                    str(widget.cget("text"))
                    for widget in _walk_widgets(preset_widgets.candidates_tab_frame)
                    if isinstance(widget, ttk.Label)
                )
                for expected_text in (
                    "c1",
                    "Fix parser",
                    "priority: high",
                    "problem one",
                    "low",
                    "impact one",
                    "app/a.py:1",
                ):
                    self.assertIn(expected_text, candidate_text)

                preset_widgets.preset_candidate_check_vars["c2"].set(True)
                MainWindow._refresh_manual_candidates_continue_button(
                    window,
                    parent.session_tab_id,
                )
                self.assertEqual(
                    "normal",
                    str(preset_widgets.preset_candidates_continue_button.cget("state")),
                )

                preset_widgets.preset_candidates_continue_button.invoke()

                self.assertEqual(
                    [(parent.session_tab_id, ("c2",))],
                    continued_calls,
                )
                self.assertEqual(
                    "disabled",
                    str(preset_widgets.preset_candidates_continue_button.cget("state")),
                )
            finally:
                if window is not None:
                    _close_tk_window(window)
                else:
                    _shutdown_runtime(runtime)

    def test_normal_session_keeps_prompt_editor(self) -> None:
        self.assertTrue(_session_kind_uses_prompt_editor(SessionTabKind.NORMAL))

    def test_preset_session_hides_prompt_editor(self) -> None:
        self.assertFalse(_session_kind_uses_prompt_editor(SessionTabKind.PRESET))

    def test_preset_session_widget_tree_smoke_with_preexisting_normal_session(self) -> None:
        with TemporaryDirectory() as storage_dir, TemporaryDirectory() as workspace_dir:
            storage_root = Path(storage_dir)
            _write_prompt_pair(storage_root, language="Python", instruction="bug")
            runtime = build_runtime(storage_root=storage_root)
            runtime.update_settings(AppSettings(executable_path="codex"))
            workspace_result = runtime.open_workspace(workspace_dir)
            workspace_tab = workspace_result.open_result.workspace_tab
            normal_session = runtime.open_session(workspace_tab.workspace_tab_id)

            window: MainWindow | None = None
            try:
                try:
                    window = MainWindow(runtime)
                except tk.TclError as error:
                    if _is_tk_display_unavailable(error):
                        self.skipTest(f"Tk display is unavailable: {error}")
                    raise

                window.withdraw()
                window.update_idletasks()
                workspace_view = window._workspace_views[workspace_tab.workspace_tab_id]
                preset_buttons = _find_widgets_by_text(workspace_view.frame, "New Preset")
                import_buttons = _find_widgets_by_text(workspace_view.frame, "Import")
                self.assertEqual(1, len(preset_buttons))
                self.assertEqual(1, len(import_buttons))

                preset_buttons[0].invoke()
                window.update_idletasks()

                preset_sessions = [
                    session_tab
                    for session_tab in runtime.list_session_tabs(
                        workspace_tab.workspace_tab_id,
                        include_closed=False,
                    )
                    if session_tab.kind == SessionTabKind.PRESET
                ]
                self.assertEqual(1, len(preset_sessions))
                preset_session = preset_sessions[0]
                self.assertEqual("P2", preset_session.display_name)

                normal_widgets = workspace_view.session_views[normal_session.session_tab_id]
                preset_widgets = workspace_view.session_views[preset_session.session_tab_id]
                normal_tab_ids = normal_widgets.body_notebook.tabs()
                preset_tab_ids = preset_widgets.body_notebook.tabs()
                self.assertEqual(
                    ("Prompt", "Progress", "History"),
                    tuple(
                        normal_widgets.body_notebook.tab(tab_id, "text")
                        for tab_id in normal_tab_ids
                    ),
                )
                self.assertEqual(
                    ("Prompt", "Progress", "History", "Candidates"),
                    tuple(
                        preset_widgets.body_notebook.tab(tab_id, "text")
                        for tab_id in preset_tab_ids
                    ),
                )
                self.assertEqual(str(normal_widgets.prompt_tab_frame), normal_tab_ids[0])
                self.assertEqual(
                    str(normal_widgets.progress_log_tab_frame),
                    normal_tab_ids[1],
                )
                self.assertEqual(
                    str(normal_widgets.history_tab_frame),
                    normal_tab_ids[2],
                )
                self.assertEqual(str(preset_widgets.prompt_tab_frame), preset_tab_ids[0])
                self.assertEqual(
                    str(preset_widgets.progress_log_tab_frame),
                    preset_tab_ids[1],
                )
                self.assertEqual(
                    str(preset_widgets.history_tab_frame),
                    preset_tab_ids[2],
                )
                self.assertEqual(
                    str(preset_widgets.candidates_tab_frame),
                    preset_tab_ids[3],
                )
                for _ in range(100):
                    runtime.process_background_events(max_items=32)
                    window._drain_runtime_events()
                    window.update()
                    if (
                        preset_widgets.preset_language_var.get() == "Python"
                        and preset_widgets.preset_instruction_var.get() == "bug"
                    ):
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(normal_widgets.prompt_text)
                self.assertIsNone(preset_widgets.prompt_text)
                self.assertIsNotNone(preset_widgets.preset_prompt_prefix_text)
                self.assertEqual(
                    "normal",
                    str(normal_widgets.ai_settings_button.cget("state")),
                )
                self.assertEqual(
                    "Codex CLI / Auto / Auto",
                    normal_widgets.execution_summary_var.get(),
                )
                run_now_buttons = _find_widgets_by_text(
                    normal_widgets.frame,
                    "Run Now",
                )
                close_session_buttons = _find_widgets_by_text(
                    normal_widgets.frame,
                    "Close Session",
                )
                self.assertEqual(1, len(run_now_buttons))
                self.assertEqual(1, len(close_session_buttons))
                self.assertIs(
                    normal_widgets.execution_summary_label.master,
                    run_now_buttons[0].master,
                )
                self.assertIs(
                    normal_widgets.execution_summary_label.master,
                    normal_widgets.exit_hook_button.master,
                )
                self.assertIs(
                    normal_widgets.execution_summary_label.master,
                    close_session_buttons[0].master,
                )
                self.assertIn(
                    str(normal_widgets.execution_summary_label.cget("width")),
                    ("", "0"),
                )
                self.assertEqual(
                    int(
                        normal_widgets.execution_summary_label.grid_info()["column"]
                    )
                    + 1,
                    int(normal_widgets.exit_hook_button.grid_info()["column"]),
                )
                self.assertEqual(
                    int(normal_widgets.exit_hook_button.grid_info()["column"]) + 1,
                    int(run_now_buttons[0].grid_info()["column"]),
                )
                self.assertEqual(
                    int(run_now_buttons[0].grid_info()["column"]) + 1,
                    int(close_session_buttons[0].grid_info()["column"]),
                )
                preset_scrolled_texts = [
                    widget
                    for widget in _walk_widgets(preset_widgets.prompt_frame)
                    if isinstance(widget, scrolledtext.ScrolledText)
                ]
                self.assertEqual(
                    [preset_widgets.preset_prompt_prefix_text],
                    preset_scrolled_texts,
                )
                self.assertEqual("Python", preset_widgets.preset_language_var.get())
                self.assertEqual("bug", preset_widgets.preset_instruction_var.get())
                self.assertEqual("medium", preset_widgets.preset_work_priority_var.get())
                preset_comboboxes = [
                    widget
                    for widget in _walk_widgets(preset_widgets.prompt_frame)
                    if isinstance(widget, ttk.Combobox)
                ]
                preset_selection_comboboxes = (
                    preset_widgets.preset_language_combobox,
                    preset_widgets.preset_instruction_combobox,
                    preset_widgets.preset_work_priority_combobox,
                )
                self.assertEqual(3, len(preset_comboboxes))
                self.assertTrue(
                    all(
                        combobox is not None
                        for combobox in preset_selection_comboboxes
                    )
                )
                self.assertIsNotNone(preset_widgets.preset_action_ai_settings_button)
                self.assertIsNotNone(
                    preset_widgets.preset_action_execution_summary_var
                )
                self.assertIsNot(
                    preset_widgets.ai_settings_button,
                    preset_widgets.preset_action_ai_settings_button,
                )
                self.assertIsNot(
                    preset_widgets.execution_summary_var,
                    preset_widgets.preset_action_execution_summary_var,
                )
                self.assertEqual(
                    preset_widgets.execution_summary_var.get(),
                    preset_widgets.preset_action_execution_summary_var.get(),
                )
                for combobox in preset_selection_comboboxes:
                    assert combobox is not None
                    self.assertEqual(PRESET_COMBOBOX_WIDTH, int(combobox.cget("width")))
                    self.assertEqual("w", str(combobox.grid_info()["sticky"]))
                self.assertEqual(
                    (0, 0, 0),
                    tuple(
                        int(combobox.grid_info()["row"])
                        for combobox in preset_selection_comboboxes
                        if combobox is not None
                    ),
                )
                assert preset_widgets.preset_action_execution_summary_label is not None
                self.assertEqual(
                    SESSION_EXECUTION_SUMMARY_WIDTH,
                    int(
                        preset_widgets.preset_action_execution_summary_label.cget(
                            "width"
                        )
                    ),
                )
                auto_commit_checkbuttons = _find_widgets_by_text(
                    preset_widgets.prompt_frame,
                    "Auto Commit",
                )
                self.assertEqual(1, len(auto_commit_checkbuttons))
                register_buttons = _find_widgets_by_text(
                    preset_widgets.prompt_frame,
                    "Add",
                )
                self.assertEqual(1, len(register_buttons))

                preset_widgets.auto_commit_var.set(True)
                assert preset_widgets.preset_prompt_prefix_text is not None
                preset_widgets.preset_prompt_prefix_text.insert(
                    "1.0",
                    "custom analysis prefix",
                )
                register_buttons[0].invoke()
                for _ in range(100):
                    runtime.process_background_events(max_items=32)
                    window._drain_runtime_events()
                    window.update()
                    if "Candidate jobs will be created" in window._status_message_var.get():
                        break
                    time.sleep(0.01)
                jobs = runtime.list_jobs(session_tab_id=preset_session.session_tab_id)
                self.assertEqual(1, len(jobs))
                self.assertTrue(jobs[0].prompt.startswith("custom analysis prefix"))
                self.assertIn("analysis prompt", jobs[0].prompt)
                self.assertIn(
                    "Candidate jobs will be created",
                    window._status_message_var.get(),
                )
                self.assertEqual(
                    str(preset_widgets.progress_log_tab_frame),
                    preset_widgets.body_notebook.select(),
                )
                self.assertEqual(
                    ("disabled", "disabled", "disabled"),
                    tuple(
                        str(combobox.cget("state"))
                        for combobox in preset_selection_comboboxes
                        if combobox is not None
                    ),
                )
                self.assertEqual(
                    "disabled",
                    str(preset_widgets.ai_settings_button.cget("state")),
                )
                assert preset_widgets.preset_action_ai_settings_button is not None
                self.assertEqual(
                    "disabled",
                    str(preset_widgets.preset_action_ai_settings_button.cget("state")),
                )
                self.assertEqual(
                    "disabled",
                    str(auto_commit_checkbuttons[0].cget("state")),
                )
                self.assertEqual(
                    "disabled",
                    str(preset_widgets.preset_prompt_prefix_text.cget("state")),
                )
                self.assertEqual("disabled", str(register_buttons[0].cget("state")))
            finally:
                if window is not None:
                    _close_tk_window(window)
                else:
                    _shutdown_runtime(runtime)


