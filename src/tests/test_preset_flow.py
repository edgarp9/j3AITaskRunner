from __future__ import annotations

import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from app.use_cases import (
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
    PresetCandidateJobsRegisteredEvent,
    RuntimeActionFailedEvent,
    _PresetAnalysisJobContext,
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


class PresetJsonParsingTests(unittest.TestCase):
    def test_parse_json_object_accepts_direct_fenced_and_embedded_object(self) -> None:
        expected = {"candidates": []}

        cases = (
            json.dumps(expected),
            "```json\n" + json.dumps(expected) + "\n```",
            "분석 결과입니다.\n" + json.dumps(expected) + "\n끝",
        )

        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(expected, parse_json_object_from_text(text))

    def test_extract_candidates_rejects_missing_candidates_array(self) -> None:
        with self.assertRaisesRegex(PresetAnalysisError, "candidates 목록"):
            extract_candidates('{"items": []}')

    def test_extract_candidates_parses_valid_candidates(self) -> None:
        response_text = _analysis_text(
            [
                _candidate_payload(
                    "1",
                    priority="HIGH",
                    evidence=["app/a.py:1", "  ", "tests/test_a.py:2"],
                )
            ]
        )

        candidates = extract_candidates(response_text)

        self.assertEqual(1, len(candidates))
        self.assertEqual("1", candidates[0].id)
        self.assertEqual("high", candidates[0].priority)
        self.assertEqual(("app/a.py:1", "tests/test_a.py:2"), candidates[0].evidence)

    def test_extract_candidates_allows_empty_candidates(self) -> None:
        self.assertEqual([], extract_candidates('{"candidates": []}'))

    def test_extract_candidates_rejects_each_missing_required_field(self) -> None:
        required_fields = ("id", "title", "problem", "evidence", "priority", "risk", "impact")

        for field in required_fields:
            with self.subTest(field=field):
                payload = _candidate_payload("1")
                payload.pop(field)

                with self.assertRaisesRegex(PresetAnalysisError, field):
                    extract_candidates(_analysis_text([payload]))

    def test_extract_candidates_rejects_empty_required_field_values(self) -> None:
        empty_values: dict[str, object] = {
            "id": " ",
            "title": "",
            "problem": " ",
            "evidence": [],
            "priority": "",
            "risk": " ",
            "impact": "",
        }

        for field, empty_value in empty_values.items():
            with self.subTest(field=field):
                payload = _candidate_payload("1")
                payload[field] = empty_value

                with self.assertRaisesRegex(PresetAnalysisError, field):
                    extract_candidates(_analysis_text([payload]))

    def test_extract_candidates_rejects_duplicate_ids(self) -> None:
        with self.assertRaisesRegex(PresetAnalysisError, "중복된 candidate id"):
            extract_candidates(
                _analysis_text(
                    [
                        _candidate_payload("1"),
                        _candidate_payload("1", priority="high"),
                    ]
                )
            )


class PresetCandidateSelectionTests(unittest.TestCase):
    def test_build_preset_analysis_prompt_describes_priority_threshold(self) -> None:
        prompt = _build_preset_analysis_prompt("Base prompt", work_priority="medium")

        self.assertIn("Base prompt", prompt)
        self.assertIn("선택된 Work Priority: medium", prompt)
        self.assertIn("Work Priority는 최소 작업 우선순위 threshold", prompt)
        self.assertIn("high는 priority=high 후보만", prompt)
        self.assertIn("medium은 priority=high 또는 priority=medium 후보", prompt)
        self.assertIn("low는 priority=high/medium/low 후보를 모두", prompt)

    def test_build_preset_analysis_prompt_prepends_analysis_prefix(self) -> None:
        prompt = _build_preset_analysis_prompt(
            "Base prompt",
            work_priority="medium",
            analysis_prompt_prefix="Prefix instructions",
        )

        self.assertTrue(prompt.startswith("Prefix instructions\n\nBase prompt"))
        self.assertIn("선택된 Work Priority: medium", prompt)

    def test_select_work_candidates_filters_by_priority_threshold(self) -> None:
        candidates = [
            _candidate("high", priority="high"),
            _candidate("medium", priority="medium"),
            _candidate("low", priority="low"),
        ]

        self.assertEqual(
            ["high"],
            [candidate.id for candidate in select_work_candidates(candidates, "high")],
        )
        self.assertEqual(
            ["high", "medium"],
            [candidate.id for candidate in select_work_candidates(candidates, "medium")],
        )
        self.assertEqual(
            ["high", "medium", "low"],
            [candidate.id for candidate in select_work_candidates(candidates, "low")],
        )

    def test_select_work_candidates_rejects_unknown_priority(self) -> None:
        with self.assertRaisesRegex(PresetAnalysisError, "high, medium, low"):
            select_work_candidates([_candidate("1")], "manual")

    def test_render_work_prompt_template_replaces_candidates_payload(self) -> None:
        candidates = [_candidate("1", priority="high")]
        rendered = render_work_prompt_template(
            "Start {{candidates_payload}}\nAgain {{ candidates_payload }}",
            candidates,
        )
        payload = build_candidates_payload(candidates)

        self.assertEqual(f"Start {payload}\nAgain {payload}", rendered)

    def test_render_work_prompt_template_preserves_payload_regex_escapes(self) -> None:
        candidates = [
            PresetCandidate(
                id="1",
                title=r"title C:\work\demo",
                problem="line one\nline two",
                evidence=r"C:\repo\app.py:10",
                priority="high",
                risk="medium",
                impact=r"literal \1 marker",
            )
        ]
        payload = build_candidates_payload(candidates)

        rendered = render_work_prompt_template("Start {{candidates_payload}}", candidates)

        self.assertEqual(f"Start {payload}", rendered)

    def test_render_work_prompt_template_rejects_missing_candidates_payload_slot(self) -> None:
        with self.assertRaisesRegex(PresetAnalysisError, "candidates_payload"):
            render_work_prompt_template("Start without payload", [_candidate("1")])


class PresetGeneratedPromptTests(unittest.TestCase):
    def test_extract_generated_work_prompts_accepts_fenced_and_embedded_responses(self) -> None:
        candidates = [_candidate("1")]
        payload = {
            "prompts": [
                {"candidate_id": "1", "title": "title 1", "prompt": "/goal one"},
            ]
        }
        cases = (
            "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```",
            "생성 결과입니다.\n" + json.dumps(payload, ensure_ascii=False) + "\n끝",
        )

        for response_text in cases:
            with self.subTest(response_text=response_text):
                prompts = extract_generated_work_prompts(response_text, candidates)

                self.assertEqual(["1"], [prompt.candidate_id for prompt in prompts])
                self.assertEqual(["/goal one"], [prompt.prompt for prompt in prompts])

    def test_extract_generated_work_prompts_returns_input_candidate_order(self) -> None:
        candidates = [_candidate("1"), _candidate("2")]
        response_text = json.dumps(
            {
                "prompts": [
                    {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                    {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                ]
            },
            ensure_ascii=False,
        )

        prompts = extract_generated_work_prompts(response_text, candidates)

        self.assertEqual(["1", "2"], [prompt.candidate_id for prompt in prompts])
        self.assertEqual(["/goal one", "/goal two"], [prompt.prompt for prompt in prompts])

    def test_extract_generated_work_prompts_rejects_missing_prompts_array(self) -> None:
        with self.assertRaisesRegex(PresetAnalysisError, "prompts 목록"):
            extract_generated_work_prompts('{"items": []}', [_candidate("1")])

    def test_extract_generated_work_prompts_rejects_missing_candidate_prompt(self) -> None:
        response_text = json.dumps(
            {"prompts": [{"candidate_id": "1", "title": "one", "prompt": "/goal one"}]},
            ensure_ascii=False,
        )

        with self.assertRaisesRegex(PresetAnalysisError, "prompts 개수"):
            extract_generated_work_prompts(response_text, [_candidate("1"), _candidate("2")])

    def test_extract_generated_work_prompts_rejects_missing_title(self) -> None:
        response_text = json.dumps(
            {"prompts": [{"candidate_id": "1", "prompt": "/goal one"}]},
            ensure_ascii=False,
        )

        with self.assertRaisesRegex(PresetAnalysisError, "title"):
            extract_generated_work_prompts(response_text, [_candidate("1")])

    def test_extract_generated_work_prompts_rejects_empty_prompt(self) -> None:
        response_text = json.dumps(
            {"prompts": [{"candidate_id": "1", "title": "one", "prompt": "  "}]},
            ensure_ascii=False,
        )

        with self.assertRaisesRegex(PresetAnalysisError, "prompt"):
            extract_generated_work_prompts(response_text, [_candidate("1")])

    def test_extract_generated_work_prompts_rejects_duplicate_candidate_prompt(self) -> None:
        response_text = json.dumps(
            {
                "prompts": [
                    {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    {"candidate_id": "1", "title": "again", "prompt": "/goal again"},
                ]
            },
            ensure_ascii=False,
        )

        with self.assertRaisesRegex(PresetAnalysisError, "중복된 candidate_id"):
            extract_generated_work_prompts(
                response_text,
                [_candidate("1"), _candidate("2")],
            )

    def test_extract_generated_work_prompts_rejects_unknown_candidate_id(self) -> None:
        response_text = json.dumps(
            {
                "prompts": [
                    {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    {"candidate_id": "extra", "title": "extra", "prompt": "/goal extra"},
                ]
            },
            ensure_ascii=False,
        )

        with self.assertRaisesRegex(PresetAnalysisError, "알 수 없는 candidate_id"):
            extract_generated_work_prompts(
                response_text,
                [_candidate("1"), _candidate("2")],
            )


class PresetUseCaseTests(unittest.TestCase):
    def test_prepare_preset_work_generation_prompt_returns_rendered_prompt(self) -> None:
        result = prepare_preset_work_generation_prompt(
            analysis_response_text=_analysis_text(
                [
                    _candidate_payload("1", priority="high"),
                    _candidate_payload("2", priority="low"),
                ]
            ),
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
        )

        self.assertTrue(result.success)
        self.assertEqual(["1"], [candidate.id for candidate in result.selected_candidates])
        self.assertIsNotNone(result.work_generation_prompt)
        self.assertIn('"id": "1"', result.work_generation_prompt or "")
        self.assertNotIn('"id": "2"', result.work_generation_prompt or "")

    def test_prepare_preset_work_generation_prompt_logs_and_returns_issue(self) -> None:
        with self.assertLogs("app.use_cases", level="ERROR"):
            result = prepare_preset_work_generation_prompt(
                analysis_response_text='{"candidates": [{"id": "1"}]}',
                work_prompt_template="work {{candidates_payload}}",
                work_priority="high",
            )

        self.assertFalse(result.success)
        self.assertIsNotNone(result.issue)
        self.assertEqual(
            "prepare_preset_work_generation_prompt",
            result.issue.operation if result.issue else "",
        )
        self.assertIn("필수 필드", result.issue.message if result.issue else "")
        self.assertNotIn("Traceback", result.issue.message if result.issue else "")

    def test_parse_preset_generated_work_prompts_returns_ordered_prompts(self) -> None:
        result = parse_preset_generated_work_prompts(
            generation_response_text=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    ]
                },
                ensure_ascii=False,
            ),
            input_candidates=(_candidate("1"), _candidate("2")),
        )

        self.assertTrue(result.success)
        self.assertEqual(["1", "2"], [prompt.candidate_id for prompt in result.prompts])

    def test_parse_preset_generated_work_prompts_logs_raw_response_and_returns_user_issue(
        self,
    ) -> None:
        raw_response = '{"prompts": [{"candidate_id": "1", "title": "one", "prompt": ""}]}'

        with self.assertLogs("app.use_cases", level="ERROR") as logs:
            result = parse_preset_generated_work_prompts(
                generation_response_text=raw_response,
                input_candidates=(_candidate("1"),),
            )

        self.assertFalse(result.success)
        self.assertIsNotNone(result.issue)
        self.assertIn("prompt", result.issue.message if result.issue else "")
        self.assertNotIn("Traceback", result.issue.message if result.issue else "")
        self.assertIn(raw_response, "\n".join(logs.output))


class PresetPromptAssetTests(unittest.TestCase):
    def test_project_prompt_store_resolves_python_bug_prompt_pair(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        store = PromptStore(project_root)

        info = store.get_instruction("Python", "bug")

        self.assertIsNotNone(info)
        self.assertEqual(
            str(project_root / "prompt" / "Python" / "bug.md"),
            info.analysis_prompt_path,
        )
        self.assertEqual(
            str(project_root / "prompt" / "Python" / "bug_work.md"),
            info.work_prompt_template_path,
        )
        self.assertTrue(store.read_analysis_prompt("Python", "bug").lstrip().startswith("/goal"))
        self.assertIn(
            "{{candidates_payload}}",
            store.read_work_prompt_template("Python", "bug"),
        )

    def test_project_prompt_store_resolves_de_abstraction_prompt_pairs(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        store = PromptStore(project_root)

        for language in ("Kotlin", "Python", "Rust", "Tauri"):
            with self.subTest(language=language):
                info = store.get_instruction(language, "de-abstraction")

                self.assertIsNotNone(info)
                self.assertEqual(language, info.language if info else "")
                self.assertEqual("de-abstraction", info.instruction if info else "")
                self.assertTrue(
                    store.read_analysis_prompt(language, "de-abstraction")
                    .lstrip()
                    .startswith("/goal")
                )
                self.assertIn(
                    "{{candidates_payload}}",
                    store.read_work_prompt_template(language, "de-abstraction"),
                )

    def test_project_prompt_tree_contains_complete_utf8_renderable_pairs(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        prompt_root = project_root / "prompt"
        store = PromptStore(project_root)
        payload = '[{"id": "sample"}]'

        expected_languages = tuple(
            path.name
            for path in sorted(prompt_root.iterdir(), key=lambda item: item.name.casefold())
            if path.is_dir() and not path.name.startswith("_")
        )

        self.assertEqual(expected_languages, tuple(store.list_languages()))
        for language in expected_languages:
            with self.subTest(language=language):
                language_dir = prompt_root / language
                analysis_files = tuple(
                    path
                    for path in sorted(
                        language_dir.glob("*.md"),
                        key=lambda item: item.name.casefold(),
                    )
                    if not path.name.endswith("_work.md")
                )
                work_files = tuple(
                    path
                    for path in sorted(
                        language_dir.glob("*_work.md"),
                        key=lambda item: item.name.casefold(),
                    )
                )
                self.assertTrue(analysis_files)
                self.assertEqual(
                    {f"{path.stem}_work.md" for path in analysis_files},
                    {path.name for path in work_files},
                )
                instructions = store.list_instructions(language)
                self.assertEqual(
                    tuple(path.stem for path in analysis_files),
                    tuple(info.instruction for info in instructions),
                )
                for instruction in (info.instruction for info in instructions):
                    with self.subTest(language=language, instruction=instruction):
                        analysis_prompt = store.read_analysis_prompt(language, instruction)
                        work_template = store.read_work_prompt_template(
                            language,
                            instruction,
                        )
                        rendered = store.render_work_prompt(
                            language,
                            instruction,
                            candidates_payload=payload,
                        )

                        self.assertTrue(analysis_prompt.lstrip().startswith("/goal"))
                        self.assertIn("{{candidates_payload}}", work_template)
                        self.assertIn("watch mode가 아닌 one-shot 명령", work_template)
                        self.assertIn("npm run dev, vite --host", work_template)
                        self.assertIn("timeout 가능한 방식으로 짧게 smoke 확인", work_template)
                        self.assertIn(payload, rendered)
                        self.assertNotIn("{{candidates_payload}}", rendered)


class PresetRuntimeFlowTests(unittest.TestCase):
    def test_preset_analysis_job_is_registered_once_per_parent_session(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            controller = AppController(
                runner=_ImmediatePresetRunner(root_path / "artifacts"),
                settings_provider=lambda: AppSettings(
                    executable_path=str(executable_path),
                ),
            )
            runtime = AppRuntime(
                controller=controller,
                repository=_RuntimeRepositoryStub(),
                prompt_store=_PresetPromptStoreStub(),
            )
            try:
                workspace = runtime.open_workspace(str(workspace_path)).open_result.workspace_tab
                parent = runtime.open_preset_session(workspace.workspace_tab_id)

                first_job = runtime.submit_preset_analysis_job(
                    parent.session_tab_id,
                    language="Python",
                    instruction="bug",
                    work_priority="medium",
                    auto_commit_enabled=True,
                )

                with self.assertRaisesRegex(ValueError, "이미 등록"):
                    runtime.submit_preset_analysis_job(
                        parent.session_tab_id,
                        language="Python",
                        instruction="bug",
                        work_priority="medium",
                        auto_commit_enabled=True,
                    )

                self.assertEqual(
                    (first_job.job_id,),
                    tuple(
                        job.job_id
                        for job in runtime.list_jobs(
                            session_tab_id=parent.session_tab_id
                        )
                    ),
                )
            finally:
                runtime.shutdown()

    def test_closed_preset_session_rejects_delayed_analysis_registration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            controller = AppController(
                runner=_ImmediatePresetRunner(root_path / "artifacts"),
                settings_provider=lambda: AppSettings(
                    executable_path=str(executable_path),
                ),
            )
            runtime = AppRuntime(
                controller=controller,
                repository=_RuntimeRepositoryStub(),
                prompt_store=_PresetPromptStoreStub(),
            )
            try:
                workspace = runtime.open_workspace(str(workspace_path)).open_result.workspace_tab
                parent = runtime.open_preset_session(workspace.workspace_tab_id)
                runtime.close_session(parent.session_tab_id)

                with self.assertRaisesRegex(ValueError, "닫힌 프리셋 세션"):
                    runtime.submit_preset_analysis_job(
                        parent.session_tab_id,
                        language="Python",
                        instruction="bug",
                        work_priority="medium",
                        auto_commit_enabled=True,
                    )

                self.assertEqual((), runtime.list_jobs(session_tab_id=parent.session_tab_id))
            finally:
                runtime.shutdown()

    def test_analysis_completion_registers_work_generation_job(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-model",
            reasoning_effort="high",
        )
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            execution_options=execution_options,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text(
                [
                    _candidate_payload("1", priority="high"),
                    _candidate_payload("2", priority="low"),
                ]
            ),
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual(
            [("preset-parent", "work " + build_candidates_payload([_candidate("1", priority="high")]))],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual([True], runtime._controller.submitted_force_fresh_sessions)
        self.assertEqual(
            [execution_options],
            runtime._controller.submitted_execution_options,
        )
        self.assertIn("Preset turn1 result captured", log_text)
        self.assertIn("Preset turn1 completed; preparing turn2", log_text)
        self.assertIn("Preset turn2 registered", log_text)
        self.assertEqual(("job-1",), runtime._controller.prioritized_job_ids)
        self.assertEqual(["workspace-1"], runtime._controller.started_queue_ids)
        self.assertEqual(["job-1"], list(runtime._preset_work_generation_job_contexts))
        self.assertTrue(
            runtime._preset_work_generation_job_contexts["job-1"].auto_commit_enabled
        )
        self.assertEqual(
            execution_options,
            runtime._preset_work_generation_job_contexts["job-1"].execution_options,
        )
        self.assertEqual(
            ("1",),
            tuple(
                candidate.id
                for candidate in runtime._preset_work_generation_job_contexts["job-1"].candidates
            ),
        )

    def test_analysis_empty_candidates_skips_turn2_without_stopping_workspace(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message='{"candidates": []}',
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual([], runtime._controller.stopped_queues)
        self.assertEqual([], runtime._event_queue.events)
        self.assertEqual({}, runtime._preset_analysis_job_contexts)
        self.assertEqual({}, runtime._preset_work_generation_job_contexts)
        self.assertIn("Preset turn2 skipped because no candidates matched work priority", log_text)

    def test_analysis_empty_response_stops_workspace_before_turn2(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message="",
        )

        with self.assertLogs("app.runtime", level="WARNING") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual(
            [("workspace-1", QueueStopReason.PRESET_FLOW_FAILED)],
            runtime._controller.stopped_queues,
        )
        self.assertIn("Preset turn2 not started because turn1 response was empty", log_text)
        self.assertIn("Preset flow stopped workspace queue", log_text)
        self.assertEqual(1, len(runtime._event_queue.events))
        self.assertIsInstance(runtime._event_queue.events[0], RuntimeActionFailedEvent)

    def test_analysis_completion_skips_turn2_when_parent_session_is_closed(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._controller.session_manager.sessions["preset-parent"] = _RuntimeSessionStub(
            "preset-parent",
            open_state=TabOpenState.CLOSED,
        )
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual([], runtime._controller.stopped_queues)
        self.assertEqual([], runtime._event_queue.events)
        self.assertIn(
            "Preset turn2 skipped because parent preset session is closed",
            "\n".join(captured_logs.output),
        )

    def test_analysis_invalid_response_stops_workspace_before_turn2(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([{"id": "1"}]),
        )

        with self.assertLogs("app.runtime", level="WARNING") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual(
            [("workspace-1", QueueStopReason.PRESET_FLOW_FAILED)],
            runtime._controller.stopped_queues,
        )
        self.assertIn("Preset turn2 not started because turn1 response could not be used", log_text)
        self.assertIn("필수 필드", log_text)
        self.assertIn("Preset flow stopped workspace queue", log_text)
        self.assertEqual(1, len(runtime._event_queue.events))
        failed_event = runtime._event_queue.events[0]
        self.assertIsInstance(failed_event, RuntimeActionFailedEvent)
        self.assertIn("필수 필드", failed_event.message)

    def test_active_workspace_generation_change_keeps_other_workspace_preset_current(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=False,
            queue_control_generation=(0, 0),
        )
        runtime._advance_queue_control_generation(None)
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        runtime._handle_preset_execution_result(event)

        self.assertFalse(runtime._queue_start_is_current(None, (0, 0)))
        self.assertTrue(runtime._queue_start_is_current("workspace-1", (0, 0)))
        self.assertEqual(
            [
                (
                    "preset-parent",
                    "work " + build_candidates_payload([_candidate("1", priority="high")]),
                )
            ],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual(["workspace-1"], runtime._controller.started_queue_ids)

    def test_active_workspace_stop_invalidates_same_workspace_pending_turn2(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._controller.workspace_manager = _ActiveWorkspaceManagerStub("workspace-1")
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=False,
            queue_control_generation=(0, 0),
        )

        runtime.stop_queue(None)
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        runtime._handle_preset_execution_result(event)

        self.assertFalse(runtime._queue_start_is_current("workspace-1", (0, 0)))
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual(
            [("workspace-1", QueueStopReason.USER_STOPPED)],
            runtime._controller.stopped_queues,
        )
        self.assertEqual({}, runtime._preset_analysis_job_contexts)

    def test_stale_enqueued_turn1_followup_clears_pending_and_rechecks_dispatch(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._controller.pending_dispatch = True
        runtime._controller.pending_dispatch_workspace_tab_ids_value = ("workspace-1",)
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=False,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        runtime._handle_preset_execution_result(event)
        runtime._advance_queue_control_generation("workspace-1")

        self.assertTrue(runtime._has_pending_preset_followup())
        self.assertEqual(1, len(request_queue.requests))
        self.assertTrue(
            runtime._runtime_action_request_is_stale(request_queue.requests[0])
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            AppRuntime._discard_runtime_action_request(request_queue.requests[0])

        self.assertFalse(runtime._has_pending_preset_followup())
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual(2, len(request_queue.requests))
        self.assertIn(
            "Preset turn1 follow-up discarded because queue generation is stale",
            "\n".join(captured_logs.output),
        )

        request_queue.requests[1].action()

        self.assertEqual(1, runtime._controller.dispatch_next_job_calls)

    def test_dispatch_waits_while_preset_turn2_registration_is_pending(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._controller.pending_dispatch = True
        runtime._controller.pending_dispatch_workspace_tab_ids_value = ("workspace-1",)
        runtime._dispatch_action_requested = True
        runtime._mark_preset_followup_pending("workspace-1")

        runtime._dispatch_next_job_for_worker()

        self.assertEqual(0, runtime._controller.dispatch_next_job_calls)
        self.assertEqual(1, len(request_queue.requests))
        self.assertTrue(runtime._dispatch_action_requested)

        runtime._clear_preset_followup_pending("workspace-1")
        request_queue.requests[0].action()

        self.assertEqual(1, runtime._controller.dispatch_next_job_calls)
        self.assertFalse(runtime._controller.pending_dispatch)

    def test_dispatch_rechecks_preset_followup_pending_after_state_lock(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._controller.pending_dispatch = True
        runtime._controller.pending_dispatch_workspace_tab_ids_value = ("workspace-1",)
        runtime._dispatch_action_requested = True
        runtime._controller_state_lock = _MarkPresetFollowupPendingOnEnterLock(
            lambda: runtime._mark_preset_followup_pending("workspace-1")
        )

        runtime._dispatch_next_job_for_worker()

        self.assertEqual(0, runtime._controller.dispatch_next_job_calls)
        self.assertTrue(runtime._has_pending_preset_followup())
        self.assertEqual(1, len(request_queue.requests))
        self.assertTrue(runtime._dispatch_action_requested)

    def test_dispatch_excludes_pending_preset_followup_workspace(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._controller.pending_dispatch = True
        runtime._controller.pending_dispatch_workspace_tab_ids_value = (
            "workspace-1",
            "workspace-2",
        )
        runtime._dispatch_action_requested = True
        runtime._mark_preset_followup_pending("workspace-1")

        runtime._dispatch_next_job_for_worker()

        self.assertEqual(1, runtime._controller.dispatch_next_job_calls)
        self.assertEqual(
            [("workspace-1",)],
            runtime._controller.dispatch_excluded_workspace_tab_ids,
        )
        self.assertFalse(runtime._controller.pending_dispatch)

    def test_running_generation_is_captured_before_user_stop_invalidates_turn2(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=False,
            queue_control_generation=(0, 0),
        )
        runtime._controller.pending_dispatch = True
        runtime._controller.running_status_events.append(
            JobStatusChangedEvent(
                job_id="analysis-job",
                workspace_tab_id="workspace-1",
                session_tab_id="preset-parent",
                previous_status=JobStatus.QUEUED,
                current_status=JobStatus.RUNNING,
                configuration_wait_reason=None,
                user_message="실행 중",
            )
        )

        runtime._dispatch_next_job_for_worker()
        runtime.stop_queue("workspace-1")
        self.assertEqual(
            (0, 0),
            runtime._preset_analysis_job_contexts[
                "analysis-job"
            ].queue_control_generation,
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertIn(
            "Preset turn1 result captured",
            "\n".join(captured_logs.output),
        )

    def test_closing_unrelated_idle_session_does_not_block_turn2_registration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            runner = _DeferredFirstPresetRunner(root_path / "artifacts")
            controller = AppController(
                runner=runner,
                settings_provider=lambda: AppSettings(
                    executable_path=str(executable_path),
                ),
            )
            runtime = AppRuntime(
                controller=controller,
                repository=_RuntimeRepositoryStub(),
                prompt_store=_PresetPromptStoreStub(),
            )
            try:
                workspace = runtime.open_workspace(str(workspace_path)).open_result.workspace_tab
                parent = runtime.open_preset_session(workspace.workspace_tab_id)
                unrelated_session = runtime.open_session(workspace.workspace_tab_id)
                analysis_job = runtime.submit_preset_analysis_job(
                    parent.session_tab_id,
                    language="Python",
                    instruction="bug",
                    work_priority="medium",
                    auto_commit_enabled=False,
                )
                runtime.start_queue(workspace.workspace_tab_id)

                self.assertTrue(
                    _drain_until(runtime, lambda: bool(runner.launched_prompts)),
                    "프리셋 턴1이 시간 안에 시작되지 않았습니다.",
                )

                runtime.close_session(unrelated_session.session_tab_id)
                runner.resolve(analysis_job.job_id)

                expected_turn2_prompt = "work " + build_candidates_payload(
                    [
                        _candidate("1", priority="high"),
                        _candidate("2", priority="medium"),
                    ]
                )
                self.assertTrue(
                    _drain_until(
                        runtime,
                        lambda: len(runner.launched_prompts) >= 2,
                    ),
                    "무관한 유휴 세션을 닫은 뒤 프리셋 턴2가 시작되지 않았습니다.",
                )
                self.assertEqual(expected_turn2_prompt, runner.launched_prompts[1])
            finally:
                runtime.shutdown()

    def test_preset_followup_pending_counts_same_workspace_actions(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._controller.pending_dispatch = True
        runtime._controller.pending_dispatch_workspace_tab_ids_value = ("workspace-1",)
        runtime._dispatch_action_requested = True

        runtime._mark_preset_followup_pending("workspace-1")
        runtime._mark_preset_followup_pending("workspace-1")
        runtime._clear_preset_followup_pending("workspace-1")

        self.assertTrue(runtime._has_pending_preset_followup())

        runtime._dispatch_next_job_for_worker()

        self.assertEqual(0, runtime._controller.dispatch_next_job_calls)
        self.assertEqual(1, len(request_queue.requests))

        runtime._clear_preset_followup_pending("workspace-1")
        request_queue.requests[0].action()

        self.assertEqual(1, runtime._controller.dispatch_next_job_calls)
        self.assertFalse(runtime._has_pending_preset_followup())

    def test_preset_turn2_registration_pending_flag_clears_after_action(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=False,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        runtime._handle_preset_execution_result(event)

        self.assertTrue(runtime._has_pending_preset_followup())
        self.assertEqual(1, len(request_queue.requests))

        request_queue.requests[0].action()

        self.assertFalse(runtime._has_pending_preset_followup())
        self.assertEqual(
            [
                (
                    "preset-parent",
                    "work " + build_candidates_payload([_candidate("1", priority="high")]),
                )
            ],
            runtime._controller.submitted_jobs,
        )

    def test_analysis_timeout_failure_with_candidates_stops_workspace(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.FAILED,
            last_message=_analysis_text(
                [
                    _candidate_payload("1", priority="high"),
                    _candidate_payload("2", priority="medium"),
                ]
            ),
        )

        with self.assertLogs("app.runtime", level="WARNING") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertIn("Preset turn2 skipped because turn1 did not complete", log_text)
        self.assertIn("Preset flow stopped workspace queue", log_text)
        self.assertEqual(
            [("workspace-1", QueueStopReason.PRESET_FLOW_FAILED)],
            runtime._controller.stopped_queues,
        )
        self.assertEqual({}, runtime._preset_analysis_job_contexts)
        self.assertEqual({}, runtime._preset_work_generation_job_contexts)
        self.assertEqual(1, len(runtime._event_queue.events))
        self.assertIsInstance(runtime._event_queue.events[0], RuntimeActionFailedEvent)

    def test_preset_analysis_continues_after_queue_stop_before_first_run(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            runner = _ImmediatePresetRunner(root_path / "artifacts")
            controller = AppController(
                runner=runner,
                settings_provider=lambda: AppSettings(
                    executable_path=str(executable_path),
                ),
            )
            runtime = AppRuntime(
                controller=controller,
                repository=_RuntimeRepositoryStub(),
                prompt_store=_PresetPromptStoreStub(),
            )
            try:
                workspace = runtime.open_workspace(str(workspace_path)).open_result.workspace_tab
                parent = runtime.open_preset_session(workspace.workspace_tab_id)

                runtime.submit_preset_analysis_job(
                    parent.session_tab_id,
                    language="Python",
                    instruction="bug",
                    work_priority="medium",
                    auto_commit_enabled=False,
                )
                runtime.stop_queue(workspace.workspace_tab_id)
                runtime.start_queue(workspace.workspace_tab_id)

                self.assertTrue(
                    _drain_until(
                        runtime,
                        lambda: len(runner.launched_prompts) >= 4,
                    ),
                    "큐 재시작 뒤 프리셋 턴2와 후보 작업이 실행되지 않았습니다.",
                )

                parent_jobs = runtime.list_jobs(session_tab_id=parent.session_tab_id)
                self.assertEqual(2, len(parent_jobs))
                self.assertTrue(all(job.status == JobStatus.COMPLETED for job in parent_jobs))
                self.assertEqual(
                    (
                        _build_preset_analysis_prompt(
                            "analysis prompt",
                            work_priority="medium",
                        ),
                        "work "
                        + build_candidates_payload(
                            [
                                _candidate("1", priority="high"),
                                _candidate("2", priority="medium"),
                            ]
                        ),
                        "/goal candidate one",
                        "/goal candidate two",
                    ),
                    tuple(runner.launched_prompts[:4]),
                )
            finally:
                runtime.shutdown()

    def test_work_generation_completion_registers_candidate_jobs_in_input_order(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-model",
            reasoning_effort="high",
        )
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"), _candidate("2")),
                auto_commit_enabled=False,
                execution_options=execution_options,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual(["preset-parent", "preset-parent"], runtime._controller.opened_parent_ids)
        self.assertEqual(
            [("candidate-1", "/goal one"), ("candidate-2", "/goal two")],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual(
            [execution_options, execution_options],
            runtime._controller.session_manager.candidate_session_execution_options,
        )
        self.assertEqual(
            [execution_options, execution_options],
            runtime._controller.submitted_execution_options,
        )
        self.assertIn("Preset turn2 result captured", log_text)
        self.assertIn("Preset turn2 completed; parsing generated prompts", log_text)
        self.assertIn("Preset turn2 parsed generated prompts", log_text)
        self.assertIn("Preset candidate jobs registered", log_text)
        self.assertEqual(("job-1", "job-2"), runtime._controller.prioritized_job_ids)
        self.assertEqual(["workspace-1"], runtime._controller.started_queue_ids)
        self.assertEqual(1, len(runtime._event_queue.events))
        registered_event = runtime._event_queue.events[0]
        self.assertIsInstance(registered_event, PresetCandidateJobsRegisteredEvent)
        self.assertEqual(("job-1", "job-2"), registered_event.registered_job_ids)
        self.assertFalse(registered_event.auto_commit_enabled)

    def test_work_generation_completion_enqueues_runtime_action_before_registration(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"), _candidate("2")),
                auto_commit_enabled=False,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        runtime._handle_preset_execution_result(event)

        self.assertEqual([], runtime._controller.opened_parent_ids)
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual([], runtime._event_queue.events)
        self.assertEqual(1, len(request_queue.requests))

        registered_event = request_queue.requests[0].action()

        self.assertIsInstance(registered_event, PresetCandidateJobsRegisteredEvent)
        self.assertEqual(
            [("candidate-1", "/goal one"), ("candidate-2", "/goal two")],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual(("job-1", "job-2"), registered_event.registered_job_ids)

    def test_work_generation_completion_skips_candidates_when_parent_session_is_closed(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._controller.session_manager.sessions["preset-parent"] = _RuntimeSessionStub(
            "preset-parent",
            open_state=TabOpenState.CLOSED,
        )
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"),),
                auto_commit_enabled=True,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)

        self.assertEqual([], runtime._controller.opened_parent_ids)
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual([], runtime._controller.stopped_queues)
        self.assertEqual([], runtime._event_queue.events)
        self.assertIn(
            "Preset candidate job registration skipped because parent preset session is closed",
            "\n".join(captured_logs.output),
        )

    def test_candidate_registration_event_survives_queue_stop_after_action(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._runtime_action_completion_queue = Queue()
        runtime._persistence_shutdown_requested = False
        runtime._persistence_shutdown_sentinel_enqueued = False
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"), _candidate("2")),
                auto_commit_enabled=False,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                        {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        runtime._handle_preset_execution_result(event)
        registered_event = request_queue.requests[0].action()
        runtime.stop_queue("workspace-1")
        runtime._runtime_action_completion_queue.put(
            _RuntimeActionCompletion(
                event=registered_event,
                queue_control_workspace_tab_id="workspace-1",
                queue_control_generation=(0, 0),
                drop_when_stale=request_queue.requests[0].drop_completion_when_stale,
            )
        )

        self.assertFalse(
            runtime._queue_start_is_current("workspace-1", (0, 0))
        )
        self.assertEqual(1, runtime._process_runtime_action_completions())
        self.assertEqual([registered_event], runtime._event_queue.events)

    def test_work_generation_timeout_failure_stops_workspace(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"), _candidate("2")),
                auto_commit_enabled=True,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.FAILED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                        {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        with self.assertLogs("app.runtime", level="WARNING") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.opened_parent_ids)
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertIn("Preset work-generation turn did not complete", log_text)
        self.assertIn("Preset flow stopped workspace queue", log_text)
        self.assertEqual(
            [("workspace-1", QueueStopReason.PRESET_FLOW_FAILED)],
            runtime._controller.stopped_queues,
        )
        self.assertEqual({}, runtime._preset_work_generation_job_contexts)
        self.assertEqual(1, len(runtime._event_queue.events))
        self.assertIsInstance(runtime._event_queue.events[0], RuntimeActionFailedEvent)

    def test_work_generation_prompt_count_mismatch_stops_workspace(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"), _candidate("2")),
                auto_commit_enabled=False,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        with self.assertLogs("app.runtime", level="WARNING") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.opened_parent_ids)
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertIn("Preset flow stopped while parsing generated work prompts", log_text)
        self.assertIn("prompts 개수", log_text)
        self.assertIn("Preset flow stopped workspace queue", log_text)
        self.assertEqual(
            [("workspace-1", QueueStopReason.PRESET_FLOW_FAILED)],
            runtime._controller.stopped_queues,
        )
        self.assertEqual({}, runtime._preset_work_generation_job_contexts)
        self.assertEqual(1, len(runtime._event_queue.events))
        failed_event = runtime._event_queue.events[0]
        self.assertIsInstance(failed_event, RuntimeActionFailedEvent)
        self.assertIn("prompts 개수", failed_event.message)

    def test_work_generation_completion_inherits_auto_commit_to_candidate_sessions(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"), _candidate("2")),
                auto_commit_enabled=True,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        runtime._handle_preset_execution_result(event)

        self.assertEqual(["preset-parent", "preset-parent"], runtime._controller.opened_parent_ids)
        self.assertEqual(
            [
                ("candidate-1", "/goal one"),
                ("candidate-1", AUTO_COMMIT_PROMPT),
                ("candidate-2", "/goal two"),
                ("candidate-2", AUTO_COMMIT_PROMPT),
            ],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual(
            ("job-1", "job-2", "job-3", "job-4"),
            runtime._controller.prioritized_job_ids,
        )
        registered_event = runtime._event_queue.events[0]
        self.assertIsInstance(registered_event, PresetCandidateJobsRegisteredEvent)
        self.assertEqual(("candidate-1", "candidate-2"), registered_event.candidate_session_tab_ids)
        self.assertEqual(("job-1", "job-2", "job-3", "job-4"), registered_event.registered_job_ids)
        self.assertTrue(registered_event.auto_commit_enabled)

    def test_p3_execution_options_reach_candidates_auto_commit_and_runner_settings(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            runner = _ImmediatePresetRunner(root_path / "artifacts")
            controller = AppController(
                runner=runner,
                settings_provider=lambda: AppSettings(
                    executable_path=str(executable_path),
                ),
            )
            runtime = AppRuntime(
                controller=controller,
                repository=_RuntimeRepositoryStub(),
                prompt_store=_PresetPromptStoreStub(),
            )
            parent_execution_options = AgentExecutionOptions(
                agent_provider="codex",
                model="gpt-5.4-mini",
                reasoning_effort="low",
            )
            candidate_execution_options = AgentExecutionOptions(
                agent_provider="codex",
                model="gpt-5",
                reasoning_effort="high",
            )
            try:
                workspace = runtime.open_workspace(str(workspace_path)).open_result.workspace_tab
                session_one = runtime.open_session(workspace.workspace_tab_id)
                session_two = runtime.open_session(workspace.workspace_tab_id)
                parent = runtime.open_preset_session(workspace.workspace_tab_id)

                self.assertEqual(
                    ("S1", "S2", "P3"),
                    tuple(
                        tab.display_name
                        for tab in runtime.list_session_tabs(workspace.workspace_tab_id)
                    ),
                )

                runtime.submit_preset_analysis_job(
                    parent.session_tab_id,
                    language="Python",
                    instruction="bug",
                    work_priority="medium",
                    auto_commit_enabled=True,
                    execution_options=parent_execution_options,
                    candidate_execution_options=candidate_execution_options,
                )
                runtime.submit_job(session_one.session_tab_id, "existing queued")
                runtime.start_queue(workspace.workspace_tab_id)

                self.assertTrue(
                    _drain_until(
                        runtime,
                        lambda: len(runner.launched_prompts) >= 7,
                    ),
                    "P3 프리셋 후보 작업 실행 순서가 시간 안에 확인되지 않았습니다.",
                )

                session_tabs = runtime.list_session_tabs(workspace.workspace_tab_id)
                self.assertEqual(
                    ("S1", "S2", "P3", "P3-1", "P3-2"),
                    tuple(tab.display_name for tab in session_tabs),
                )
                self.assertEqual(
                    (session_one.session_tab_id, session_two.session_tab_id),
                    tuple(tab.session_tab_id for tab in session_tabs[:2]),
                )

                candidate_tabs = tuple(
                    tab
                    for tab in session_tabs
                    if tab.parent_session_tab_id == parent.session_tab_id
                )
                self.assertEqual(
                    (SessionTabKind.PRESET_CANDIDATE, SessionTabKind.PRESET_CANDIDATE),
                    tuple(tab.kind for tab in candidate_tabs),
                )

                parent_jobs = runtime.list_jobs(session_tab_id=parent.session_tab_id)
                self.assertEqual(2, len(parent_jobs))
                self.assertNotIn(AUTO_COMMIT_PROMPT, tuple(job.prompt for job in parent_jobs))
                self.assertTrue(all(job.status == JobStatus.COMPLETED for job in parent_jobs))
                self.assertEqual(
                    (parent_execution_options, parent_execution_options),
                    tuple(job.execution_options for job in parent_jobs),
                )
                locked_parent = runtime.get_session_tab(parent.session_tab_id)
                self.assertTrue(locked_parent.execution_options_locked)
                self.assertEqual(parent_execution_options, locked_parent.execution_options)
                self.assertEqual(
                    (candidate_execution_options, candidate_execution_options),
                    tuple(tab.execution_options for tab in candidate_tabs),
                )
                self.assertTrue(
                    all(tab.execution_options_locked for tab in candidate_tabs)
                )

                candidate_job_prompts = tuple(
                    tuple(job.prompt for job in runtime.list_jobs(session_tab_id=tab.session_tab_id))
                    for tab in candidate_tabs
                )
                self.assertEqual(
                    (
                        ("/goal candidate one", AUTO_COMMIT_PROMPT),
                        ("/goal candidate two", AUTO_COMMIT_PROMPT),
                    ),
                    candidate_job_prompts,
                )
                candidate_job_execution_options = tuple(
                    tuple(
                        job.execution_options
                        for job in runtime.list_jobs(session_tab_id=tab.session_tab_id)
                    )
                    for tab in candidate_tabs
                )
                self.assertEqual(
                    (
                        (candidate_execution_options, candidate_execution_options),
                        (candidate_execution_options, candidate_execution_options),
                    ),
                    candidate_job_execution_options,
                )
                self.assertEqual(
                    (
                        _build_preset_analysis_prompt(
                            "analysis prompt",
                            work_priority="medium",
                        ),
                        "work "
                        + build_candidates_payload(
                            [
                                _candidate("1", priority="high"),
                                _candidate("2", priority="medium"),
                            ]
                        ),
                        "/goal candidate one",
                        AUTO_COMMIT_PROMPT,
                        "/goal candidate two",
                        AUTO_COMMIT_PROMPT,
                        "existing queued",
                    ),
                    tuple(runner.launched_prompts[:7]),
                )
                self.assertEqual(
                    (
                        ("codex", "gpt-5.4-mini", "low"),
                        ("codex", "gpt-5.4-mini", "low"),
                        ("codex", "gpt-5", "high"),
                        ("codex", "gpt-5", "high"),
                        ("codex", "gpt-5", "high"),
                        ("codex", "gpt-5", "high"),
                    ),
                    tuple(
                        (
                            options.agent_provider,
                            options.model,
                            options.reasoning_effort,
                        )
                        for options in runner.launched_execution_options[:6]
                    ),
                )
            finally:
                runtime.shutdown()

    def test_p2_completion_creates_candidate_sessions_and_runs_them_first(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            runner = _ImmediatePresetRunner(root_path / "artifacts")
            controller = AppController(
                runner=runner,
                settings_provider=lambda: AppSettings(
                    executable_path=str(executable_path),
                ),
            )
            runtime = AppRuntime(
                controller=controller,
                repository=_RuntimeRepositoryStub(),
                prompt_store=_PresetPromptStoreStub(),
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
                    "프리셋 후보 작업 실행 순서가 시간 안에 확인되지 않았습니다.",
                )

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
                self.assertNotIn(
                    "커밋해 주세요.",
                    tuple(job.prompt for job in parent_jobs),
                )
                self.assertTrue(all(job.status == JobStatus.COMPLETED for job in parent_jobs))
                self.assertEqual(2, len(parent_jobs))

                candidate_job_prompts = tuple(
                    runtime.list_jobs(session_tab_id=tab.session_tab_id)[0].prompt
                    for tab in candidate_tabs
                )
                self.assertEqual(("/goal candidate one", "/goal candidate two"), candidate_job_prompts)
                candidate_job_counts = tuple(
                    len(runtime.list_jobs(session_tab_id=tab.session_tab_id))
                    for tab in candidate_tabs
                )
                self.assertEqual((2, 2), candidate_job_counts)
                self.assertEqual(
                    (
                        _build_preset_analysis_prompt(
                            "analysis prompt",
                            work_priority="medium",
                            analysis_prompt_prefix="custom analysis prefix",
                        ),
                        "work "
                        + build_candidates_payload(
                            [
                                _candidate("1", priority="high"),
                                _candidate("2", priority="medium"),
                            ]
                        ),
                        "/goal candidate one",
                        AUTO_COMMIT_PROMPT,
                        "/goal candidate two",
                        AUTO_COMMIT_PROMPT,
                        "existing queued",
                    ),
                    tuple(runner.launched_prompts[:7]),
                )
            finally:
                runtime.shutdown()

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


if __name__ == "__main__":
    unittest.main()

