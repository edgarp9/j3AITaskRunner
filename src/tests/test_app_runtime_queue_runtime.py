from __future__ import annotations

from tests._app_runtime_helpers import *


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

    def test_update_settings_queue_mode_change_clears_runtime_jobs(self) -> None:
        controller = _RuntimeSettingsControllerStub(
            jobs=(
                _RuntimeJobStub(job_id="job-1", status=JobStatus.QUEUED),
                _RuntimeJobStub(job_id="job-2", status=JobStatus.COMPLETED),
            ),
        )
        repository = _RuntimePersistenceRepositoryStub()
        runtime = AppRuntime(controller=controller, repository=repository)
        updated_settings = AppSettings(queue_mode="shared")

        result = runtime.update_settings(updated_settings)

        self.assertTrue(result.queue_mode_changed)
        self.assertEqual(2, result.cleared_job_count)
        self.assertEqual(2, controller.cleared_job_count)
        self.assertEqual((), controller.scheduler.list_jobs())
        self.assertEqual(updated_settings, runtime.settings)
        self.assertEqual([], controller.retried_job_ids)
        self.assertTrue(
            _wait_until(lambda: repository.saved_settings == [updated_settings]),
            "background settings save did not complete",
        )

    def test_update_settings_queue_mode_change_rejects_running_job(self) -> None:
        controller = _RuntimeSettingsControllerStub(
            jobs=(
                _RuntimeJobStub(job_id="job-1", status=JobStatus.RUNNING),
            ),
        )
        runtime = AppRuntime(
            controller=controller,
            repository=_RuntimePersistenceRepositoryStub(),
        )

        with self.assertRaises(ValueError):
            runtime.update_settings(AppSettings(queue_mode="shared"))

        self.assertEqual(AppSettings(), runtime.settings)
        self.assertEqual(0, controller.cleared_job_count)

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

