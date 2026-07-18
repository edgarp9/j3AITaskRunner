from __future__ import annotations

from tests._preset_flow_helpers import *

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
    def test_manual_is_valid_preset_work_priority_option(self) -> None:
        self.assertIn("manual", PRESET_WORK_PRIORITY_OPTIONS)

    def test_build_preset_analysis_prompt_describes_priority_threshold(self) -> None:
        prompt = _build_preset_analysis_prompt("Base prompt", work_priority="medium")

        self.assertIn("Base prompt", prompt)
        self.assertIn("선택된 Work Priority: medium", prompt)
        self.assertIn("Work Priority는 최소 작업 우선순위 threshold", prompt)
        self.assertIn("high는 priority=high 후보만", prompt)
        self.assertIn("medium은 priority=high 또는 priority=medium 후보", prompt)
        self.assertIn("low는 priority=high/medium/low 후보를 모두", prompt)

    def test_build_preset_analysis_prompt_manual_includes_all_candidates(self) -> None:
        prompt = _build_preset_analysis_prompt("Base prompt", work_priority="manual")

        self.assertIn("선택된 Work Priority: manual", prompt)
        self.assertIn("UI에서 사용자가 후보를 직접 선택", prompt)
        self.assertIn("priority threshold로 후보를 제외하지 말고", prompt)
        self.assertIn("high/medium/low 후보를 모두 candidates에 포함", prompt)

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

    def test_select_manual_work_candidates_keeps_analysis_order_and_ignores_unknown_ids(
        self,
    ) -> None:
        candidates = [_candidate("1"), _candidate("2"), _candidate("3")]

        selected = select_manual_work_candidates(
            candidates,
            ["3", "missing", "1", "3"],
        )

        self.assertEqual(["1", "3"], [candidate.id for candidate in selected])

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

    def test_prepare_manual_preset_work_generation_prompt_uses_selected_ids_in_order(
        self,
    ) -> None:
        result = prepare_manual_preset_work_generation_prompt(
            analysis_response_text=_analysis_text(
                [
                    _candidate_payload("1", priority="high"),
                    _candidate_payload("2", priority="medium"),
                    _candidate_payload("3", priority="low"),
                ]
            ),
            work_prompt_template="work {{candidates_payload}}",
            selected_candidate_ids=("3", "1", "missing", "3"),
        )

        self.assertTrue(result.success)
        self.assertEqual(
            ["1", "3"],
            [candidate.id for candidate in result.selected_candidates],
        )
        self.assertIsNotNone(result.work_generation_prompt)
        self.assertIn('"id": "1"', result.work_generation_prompt or "")
        self.assertIn('"id": "3"', result.work_generation_prompt or "")
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

