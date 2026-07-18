from __future__ import annotations

from tests._preset_flow_helpers import *

class PresetRuntimeFlowTests(unittest.TestCase):
    def test_python_bug_prompt_assets_drive_p2_candidate_pipeline_with_fake_runner(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        prompt_store = PromptStore(project_root)
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            runner = _PromptAssetPresetRunner(root_path / "artifacts")
            controller = AppController(
                runner=runner,
                settings_provider=lambda: AppSettings(
                    executable_path=str(executable_path),
                ),
            )
            runtime = AppRuntime(
                controller=controller,
                repository=_RuntimeRepositoryStub(),
                prompt_store=prompt_store,
            )
            try:
                workspace = runtime.open_workspace(str(workspace_path)).open_result.workspace_tab
                runtime.open_session(workspace.workspace_tab_id)
                parent = runtime.open_preset_session(workspace.workspace_tab_id)
                existing_session = runtime.open_session(workspace.workspace_tab_id)

                self.assertEqual("P2", parent.display_name)
                runtime.submit_preset_analysis_job(
                    parent.session_tab_id,
                    language="Python",
                    instruction="bug",
                    work_priority="medium",
                    analysis_prompt_prefix="custom analysis prefix",
                    auto_commit_enabled=True,
                )
                runtime.submit_job(existing_session.session_tab_id, "existing queued")
                runtime.start_queue(workspace.workspace_tab_id)

                self.assertTrue(
                    _drain_until(
                        runtime,
                        lambda: len(runner.launched_prompts) >= 7,
                    ),
                    "실제 bug 프롬프트 자산 기반 후보 작업 실행 순서가 확인되지 않았습니다.",
                )

                expected_analysis_prompt = prompt_store.read_analysis_prompt(
                    "Python",
                    "bug",
                ).strip()
                self.assertTrue(
                    runner.launched_prompts[0].startswith(
                        f"custom analysis prefix\n\n{expected_analysis_prompt}"
                    )
                )
                self.assertIn("선택된 Work Priority: medium", runner.launched_prompts[0])
                self.assertIn("다음 입력 후보 각각에 대해", runner.launched_prompts[1])
                self.assertNotIn("custom analysis prefix", runner.launched_prompts[1])
                self.assertIn('"id": "1"', runner.launched_prompts[1])
                self.assertIn('"id": "2"', runner.launched_prompts[1])
                self.assertNotIn('"id": "3"', runner.launched_prompts[1])
                self.assertNotIn("{{candidates_payload}}", runner.launched_prompts[1])

                session_tabs = runtime.list_session_tabs(workspace.workspace_tab_id)
                candidate_tabs = tuple(
                    tab
                    for tab in session_tabs
                    if tab.parent_session_tab_id == parent.session_tab_id
                )
                self.assertEqual(("P2-1", "P2-2"), tuple(tab.display_name for tab in candidate_tabs))
                self.assertEqual(
                    (SessionTabKind.PRESET_CANDIDATE, SessionTabKind.PRESET_CANDIDATE),
                    tuple(tab.kind for tab in candidate_tabs),
                )

                parent_jobs = runtime.list_jobs(session_tab_id=parent.session_tab_id)
                self.assertEqual(2, len(parent_jobs))
                self.assertNotIn(AUTO_COMMIT_PROMPT, tuple(job.prompt for job in parent_jobs))

                candidate_job_prompts = tuple(
                    tuple(job.prompt for job in runtime.list_jobs(session_tab_id=tab.session_tab_id))
                    for tab in candidate_tabs
                )
                self.assertEqual(
                    (
                        ("/goal prompt asset candidate one", AUTO_COMMIT_PROMPT),
                        ("/goal prompt asset candidate two", AUTO_COMMIT_PROMPT),
                    ),
                    candidate_job_prompts,
                )
                self.assertEqual(
                    (
                        runner.launched_prompts[0],
                        runner.launched_prompts[1],
                        "/goal prompt asset candidate one",
                        AUTO_COMMIT_PROMPT,
                        "/goal prompt asset candidate two",
                        AUTO_COMMIT_PROMPT,
                        "existing queued",
                    ),
                    tuple(runner.launched_prompts[:7]),
                )
                self.assertEqual(
                    (
                        runner.launched_prompts[0],
                        runner.launched_prompts[1],
                        "/goal prompt asset candidate one",
                        AUTO_COMMIT_PROMPT,
                        "/goal prompt asset candidate two",
                        AUTO_COMMIT_PROMPT,
                        "existing queued",
                    ),
                    tuple(
                        job.prompt
                        for job in runtime.list_workspace_jobs(workspace.workspace_tab_id)
                    ),
                )
            finally:
                runtime.shutdown()

