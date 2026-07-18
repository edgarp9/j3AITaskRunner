from __future__ import annotations

from tests._process_runner_helpers import *

class CodexCliProcessRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.runner_root = Path(self.temp_dir.name)
        self.request = CodexRunRequest(
            job_id="job-1",
            workspace_path=self.temp_dir.name,
            prompt="Solve this task.",
            operational_settings=AppSettings(
                executable_path=str(_create_fake_executable(self.runner_root, "codex.exe")),
                file_logging_enabled=True,
            ),
            execution_options=AgentExecutionOptions(
                model="gpt-5.4",
                reasoning_effort="high",
            ),
            session_id=None,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_marks_success_with_completed_turn_and_last_message(self) -> None:
        stdout_lines = (
            '{"type":"thread.started","thread_id":"thread-123"}\n',
            '{"type":"progress","message":"'
            + ("full progress payload " * 12)
            + '"}\n',
            '{"type":"turn.completed"}\n',
        )
        stderr_lines = (
            "warning: diagnostic only\n",
            "full stderr diagnostic " * 12 + "\n",
        )
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=stdout_lines,
                stderr_lines=stderr_lines,
                exit_code=0,
                last_message_text="Final answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertTrue(result.success)
        self.assertEqual("thread-123", result.session_id)
        self.assertEqual("Final answer", result.last_message)
        self.assertEqual("Solve this task.", result.artifacts.prompt_path.read_text(encoding="utf-8"))
        self.assertEqual(
            "".join(stdout_lines),
            result.artifacts.stdout_jsonl_path.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            "".join(stderr_lines),
            result.artifacts.stderr_log_path.read_text(encoding="utf-8"),
        )

        metadata = json.loads(result.artifacts.launch_metadata_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", metadata["result_status"])
        self.assertEqual("thread-123", metadata["resolved_session_id"])
        self.assertEqual(str(Path(self.request.workspace_path).resolve()), metadata["process_cwd"])
        self.assertEqual(
            str(Path(self.request.workspace_path).resolve()),
            factory.kwargs_calls[0]["cwd"],
        )
        expected_creationflags = process_runner._hidden_console_creationflags()
        if expected_creationflags:
            self.assertEqual(
                expected_creationflags,
                factory.kwargs_calls[0]["creationflags"],
            )
        else:
            self.assertNotIn("creationflags", factory.kwargs_calls[0])

        process = factory.instances[0]
        self.assertEqual("Solve this task.", process.stdin.content)
        self.assertTrue(process.stdin.closed)

    def test_run_with_file_logging_disabled_keeps_ui_parsing_without_log_artifacts(self) -> None:
        request = CodexRunRequest(
            job_id="job-no-file-log",
            workspace_path=self.temp_dir.name,
            prompt="Solve without files.",
            operational_settings=AppSettings(
                executable_path=self.request.operational_settings.executable_path,
                file_logging_enabled=False,
            ),
            execution_options=AgentExecutionOptions(
                model="gpt-5.4",
                reasoning_effort="high",
            ),
            session_id=None,
        )
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-no-file"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                stderr_lines=("diagnostic not saved\n",),
                exit_code=0,
                last_message_text="Final answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertEqual("thread-no-file", result.session_id)
        self.assertEqual("Final answer", result.last_message)
        self.assertFalse((self.runner_root / "artifacts").exists())
        self.assertFalse(result.artifacts.root_dir.exists())
        self.assertFalse(result.artifacts.prompt_path.exists())
        self.assertFalse(result.artifacts.stdout_jsonl_path.exists())
        self.assertFalse(result.artifacts.stderr_log_path.exists())
        self.assertFalse(result.artifacts.launch_metadata_path.exists())
        self.assertFalse(result.artifacts.last_message_path.exists())

    def test_run_keeps_stdout_parsing_when_artifact_file_logging_fails(self) -> None:
        original_open = Path.open

        for failure_mode in ("open", "write", "flush"):
            with self.subTest(failure_mode=failure_mode):
                request = CodexRunRequest(
                    job_id=f"job-artifact-{failure_mode}",
                    workspace_path=self.temp_dir.name,
                    prompt="Solve with artifact failure.",
                    operational_settings=self.request.operational_settings,
                    session_id=None,
                )
                stdout_lines = _stdout_lines_for_artifact_failure(failure_mode)
                factory = _FakePopenFactory(
                    _FakeProcessScenario(
                        stdout_lines=stdout_lines,
                        exit_code=0,
                        last_message_text="Final answer",
                    )
                )
                runner = CodexCliProcessRunner(
                    self.runner_root / "artifacts",
                    popen_factory=factory,
                )
                parsed_event_types: list[str] = []
                forwarded_stdout_lines: list[str] = []

                def fail_stdout_artifact_open(path: Path, *args: object, **kwargs: object):
                    mode = str(args[0] if args else kwargs.get("mode", "r"))
                    if path.name == "stdout.jsonl" and "a" in mode:
                        if failure_mode == "open":
                            raise OSError("artifact open failed")
                        return _FailingArtifactFile(failure_mode)
                    return original_open(path, *args, **kwargs)

                with mock.patch.object(Path, "open", new=fail_stdout_artifact_open):
                    with self.assertLogs("infra.process_runner", level="ERROR") as logs:
                        handle = runner.launch(
                            request,
                            on_stdout_line=forwarded_stdout_lines.append,
                            on_json_event=lambda event: parsed_event_types.append(
                                event.event_type
                            ),
                        )
                        result = handle.wait()

                self.assertEqual(AgentRunStatus.COMPLETED, result.status)
                self.assertEqual(f"thread-{failure_mode}", result.session_id)
                self.assertTrue(result.parser_summary.saw_turn_completed)
                self.assertIn("thread.started", parsed_event_types)
                self.assertIn("turn.completed", parsed_event_types)
                self.assertEqual(stdout_lines, tuple(forwarded_stdout_lines))
                self.assertTrue(
                    any("artifact file" in message for message in logs.output),
                    logs.output,
                )

    def test_validate_allows_resume_without_inspecting_external_session_files(self) -> None:
        request = CodexRunRequest(
            job_id="job-resume",
            workspace_path=self.temp_dir.name,
            prompt="follow up",
            operational_settings=self.request.operational_settings,
            session_id="thread-123",
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts")

        self.assertIsNone(runner.validate(request))

    def test_validate_accepts_path_command_name(self) -> None:
        request = CodexRunRequest(
            job_id="job-command",
            workspace_path=self.temp_dir.name,
            prompt="hello",
            operational_settings=AppSettings(executable_path="codex"),
            session_id=None,
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts")

        with mock.patch("infra.executable.shutil.which", return_value=sys.executable):
            self.assertIsNone(runner.validate(request))

    def test_validate_accepts_codex_cli_directory(self) -> None:
        executable = self.runner_root / "codex-x86_64-unknown-linux-musl"
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o755)
        request = CodexRunRequest(
            job_id="job-directory",
            workspace_path=self.temp_dir.name,
            prompt="hello",
            operational_settings=AppSettings(executable_path=str(self.runner_root)),
            session_id=None,
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts")

        self.assertIsNone(runner.validate(request))

    def test_run_records_resume_mode_without_session_metadata(self) -> None:
        request = CodexRunRequest(
            job_id="job-resume",
            workspace_path=self.temp_dir.name,
            prompt="follow up",
            operational_settings=self.request.operational_settings,
            session_id="thread-123",
        )
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                exit_code=0,
                last_message_text="Follow-up answer",
            )
        )
        runner = CodexCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertNotIn("-C", factory.calls[0])
        self.assertEqual(
            str(Path(self.temp_dir.name).resolve()),
            factory.kwargs_calls[0]["cwd"],
        )
        launch_metadata = json.loads(
            result.artifacts.launch_metadata_path.read_text(encoding="utf-8")
        )
        self.assertEqual("resume", launch_metadata["mode"])
        self.assertEqual("thread-123", launch_metadata["session_id"])
        self.assertNotIn("session_metadata_cwd", launch_metadata)
        self.assertNotIn("session_metadata_path", launch_metadata)

    def test_run_keeps_started_process_when_stdin_close_raises_oserror(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                exit_code=0,
                last_message_text="Final answer",
                stdin_close_error=BrokenPipeError("broken pipe"),
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        with self.assertLogs("infra.process_runner", level="WARNING"):
            result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        process = factory.instances[0]
        self.assertEqual("Solve this task.", process.stdin.content)
        self.assertTrue(process.stdin.closed)
        self.assertFalse(process.terminated)
        self.assertIn(
            "STDIN_CLOSE_ERROR: broken pipe",
            result.artifacts.stderr_log_path.read_text(encoding="utf-8"),
        )

    def test_launch_exposes_handle_before_stdin_write_and_honors_early_cancel(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                exit_code=0,
                last_message_text="Final answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        def cancel_before_stdin_write(handle) -> None:
            handle.terminate(timeout=0)

        handle = runner.launch(self.request, on_handle_created=cancel_before_stdin_write)
        result = handle.wait()

        process = factory.instances[0]
        self.assertEqual(AgentRunStatus.CANCELED, result.status)
        self.assertTrue(process.terminated)
        self.assertEqual("", process.stdin.content)
        self.assertTrue(process.stdin.closed)
        self.assertIn(
            "STDIN_WRITE_CANCELED",
            result.artifacts.stderr_log_path.read_text(encoding="utf-8"),
        )

    def test_run_marks_failed_on_turn_failed_event(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-999"}\n',
                    '{"type":"turn.failed","message":"tool execution failed"}\n',
                ),
                stderr_lines=("warning only\n",),
                exit_code=0,
                last_message_text="ignored",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertEqual("thread-999", result.session_id)
        self.assertEqual("tool execution failed", result.failure_reason)

    def test_run_allows_transient_error_when_turn_completed(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"turn.started"}\n',
                    '{"type":"error","message":"Reconnecting... 2/5 (request timed out)"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                exit_code=0,
                last_message_text="Final answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertEqual("Final answer", result.last_message)
        self.assertTrue(result.parser_summary.has_error_event)
        self.assertFalse(result.parser_summary.has_failure_event)
        metadata = json.loads(result.artifacts.launch_metadata_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", metadata["result_status"])
        self.assertTrue(metadata["has_error_event"])
        self.assertFalse(metadata["has_failure_event"])

    def test_run_marks_failed_on_error_without_completed_turn(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"error","message":"model stream failed"}\n',
                ),
                exit_code=0,
                last_message_text="Partial answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertEqual("model stream failed", result.failure_reason)

    def test_run_marks_failed_on_abnormal_exit_without_failure_event(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=('{"type":"thread.started","thread_id":"thread-321"}\n',),
                stderr_lines=("stderr noise\n",),
                exit_code=7,
                last_message_text=None,
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertIn("exit_code=7", result.failure_reason or "")

    def test_run_returns_failed_result_when_process_start_raises_oserror(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                raise_error=OSError("spawn failed"),
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        with self.assertLogs("infra.process_runner", level="ERROR"):
            result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertIn("spawn failed", result.failure_reason or "")
        self.assertIn(
            "PROCESS_START_ERROR",
            result.artifacts.stderr_log_path.read_text(encoding="utf-8"),
        )

    def test_launch_terminates_process_when_launch_metadata_update_fails_after_spawn(self) -> None:
        factory = _FakePopenFactory(_FakeProcessScenario())
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        call_count = 0

        def fail_on_second_write(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("launch metadata rewrite failed")

        with mock.patch("infra.process_runner._write_json_file", side_effect=fail_on_second_write):
            with self.assertRaisesRegex(OSError, "launch metadata rewrite failed"):
                runner.launch(self.request)

        process = factory.instances[0]
        self.assertTrue(process.terminated)
        self.assertTrue(process.stdin.closed)
        self.assertEqual({}, runner._active_handles)

    def test_launch_cleans_up_process_and_active_handle_when_handle_start_fails(self) -> None:
        factory = _FakePopenFactory(_FakeProcessScenario())
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        with mock.patch(
            "infra.process_runner.RunningCodexProcess.start",
            side_effect=RuntimeError("start failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "start failed"):
                runner.launch(self.request)

        process = factory.instances[0]
        self.assertTrue(process.terminated)
        self.assertTrue(process.stdin.closed)
        self.assertEqual({}, runner._active_handles)










