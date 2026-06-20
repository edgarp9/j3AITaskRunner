from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.use_cases import load_bootstrap_data, save_app_settings, save_saved_workspaces
from domain import (
    DEFAULT_AGENT_PROVIDER,
    AgentExecutionOptions,
    AppSettings,
    SavedWorkspace,
    execution_options_from_settings,
)
from infra.repository import (
    LocalJsonRepository,
    PersistenceLoadError,
    PersistenceSaveError,
    PromptStore,
    PromptStoreError,
)

PERSISTENCE_FILE_NAME = "j3AITaskRunner.json"


def _dt(hour: int) -> datetime:
    return datetime(2026, 4, 22, hour, tzinfo=timezone.utc)


def _unsafe_settings_with_value(field_name: str, value: object) -> AppSettings:
    """Build a corrupted settings object to exercise repository boundary checks."""
    settings = object.__new__(AppSettings)
    for safe_field_name, safe_value in {
        "executable_path": None,
        "executable_paths": {},
        "output_font_size": 12,
        "execution_timeout_minutes": 120,
        "inactivity_timeout_minutes": 30,
        "termination_grace_seconds": 5,
        "file_logging_enabled": True,
        "ui_language": "en",
        "agent_provider": DEFAULT_AGENT_PROVIDER,
        "default_model": "",
        "default_reasoning_effort": "",
    }.items():
        object.__setattr__(settings, safe_field_name, safe_value)
    object.__setattr__(settings, field_name, value)
    return settings


class PromptStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_prompt_root_prefers_app_prompt_and_falls_back_to_lib_prompt(self) -> None:
        (self.root_path / "lib" / "prompt" / "Rust").mkdir(parents=True)
        (self.root_path / "prompt" / "Python").mkdir(parents=True)

        store = PromptStore(self.root_path)

        self.assertEqual(self.root_path / "prompt", store.prompt_root)
        self.assertEqual(["Python"], store.list_languages())

        with TemporaryDirectory() as fallback_dir:
            fallback_root = Path(fallback_dir)
            (fallback_root / "lib" / "prompt" / "Tauri").mkdir(parents=True)

            fallback_store = PromptStore(fallback_root)

            self.assertEqual(fallback_root / "lib" / "prompt", fallback_store.prompt_root)
            self.assertEqual(["Tauri"], fallback_store.list_languages())

    def test_prompt_root_uses_pyinstaller_collected_prompt_when_app_roots_are_missing(
        self,
    ) -> None:
        with TemporaryDirectory() as bundle_dir:
            bundle_root = Path(bundle_dir)
            (bundle_root / "prompt" / "Kotlin").mkdir(parents=True)

            with patch("infra.repository.sys._MEIPASS", str(bundle_root), create=True):
                store = PromptStore(self.root_path)

            self.assertEqual(bundle_root / "prompt", store.prompt_root)
            self.assertEqual(["Kotlin"], store.list_languages())

    def test_lists_languages_and_only_paired_instructions(self) -> None:
        self._write_prompt_pair("Python", "bug", analysis="analysis", work="work")
        self._write_prompt_pair("Rust", "refactor", analysis="analysis", work="work")
        python_dir = self.root_path / "prompt" / "Python"
        (python_dir / "orphan.md").write_text("missing work pair", encoding="utf-8")
        (python_dir / "work_only_work.md").write_text(
            "missing analysis pair",
            encoding="utf-8",
        )

        store = PromptStore(self.root_path)

        self.assertEqual(["Python", "Rust"], store.list_languages())
        python_instructions = store.list_instructions("Python")
        self.assertEqual(["bug"], [item.instruction for item in python_instructions])
        self.assertTrue(store.has_instruction_pair("Python", "bug"))
        self.assertFalse(store.has_instruction_pair("Python", "orphan"))
        self.assertIsNone(store.get_instruction("Python", "work_only"))

        info = python_instructions[0]
        self.assertEqual("Python", info.language)
        self.assertEqual("bug", info.instruction)
        self.assertEqual(str(python_dir / "bug.md"), info.analysis_prompt_path)
        self.assertEqual(str(python_dir / "bug_work.md"), info.work_prompt_template_path)

    def test_reads_prompts_and_replaces_candidates_payload(self) -> None:
        payload = json.dumps(
            {
                "candidates": [
                    {
                        "path": r"C:\repo\app.py",
                        "problem": "line one\nline two",
                        "impact": r"literal \1 marker",
                    }
                ]
            },
            indent=2,
        )
        self._write_prompt_pair(
            "Python",
            "optimize",
            analysis="analysis prompt",
            work="Start {{candidates_payload}}\nAgain {{ candidates_payload }}",
        )
        store = PromptStore(self.root_path)

        self.assertEqual(
            "analysis prompt",
            store.read_analysis_prompt("Python", "optimize"),
        )
        self.assertEqual(
            f"Start {payload}\nAgain {payload}",
            store.render_work_prompt("Python", "optimize", candidates_payload=payload),
        )

    def test_reading_non_utf8_prompt_raises_prompt_store_error(self) -> None:
        language_dir = self.root_path / "prompt" / "Python"
        language_dir.mkdir(parents=True)
        (language_dir / "bug.md").write_bytes(b"\xff\xfe\x00")
        (language_dir / "bug_work.md").write_text(
            "work {{candidates_payload}}",
            encoding="utf-8",
        )
        store = PromptStore(self.root_path)

        with self.assertRaises(PromptStoreError):
            store.read_analysis_prompt("Python", "bug")

    def test_render_work_prompt_rejects_template_without_candidates_payload(self) -> None:
        self._write_prompt_pair(
            "Python",
            "bug",
            analysis="analysis",
            work="work without payload slot",
        )
        store = PromptStore(self.root_path)

        with self.assertRaisesRegex(PromptStoreError, "candidates_payload"):
            store.render_work_prompt("Python", "bug", candidates_payload="[]")

    def test_rejects_unsafe_language_or_instruction_names(self) -> None:
        self._write_prompt_pair("Python", "bug", analysis="analysis", work="work")
        store = PromptStore(self.root_path)
        unsafe_values = (
            "",
            ".",
            "..",
            " Python",
            "Python ",
            "Python/bug",
            r"Python\bug",
            str(self.root_path / "prompt"),
            "C:prompt",
            "bad:name",
            'bad"name',
            "bad*name",
            "CON",
            "LPT1.txt",
            "line\nbreak",
        )

        for unsafe_value in unsafe_values:
            with self.subTest(language=unsafe_value):
                with self.assertRaises(ValueError):
                    store.list_instructions(unsafe_value)

        for unsafe_value in unsafe_values:
            with self.subTest(instruction=unsafe_value):
                with self.assertRaises(ValueError):
                    store.get_instruction("Python", unsafe_value)

    def _write_prompt_pair(
        self,
        language: str,
        instruction: str,
        *,
        analysis: str,
        work: str,
    ) -> None:
        language_dir = self.root_path / "prompt" / language
        language_dir.mkdir(parents=True, exist_ok=True)
        (language_dir / f"{instruction}.md").write_text(analysis, encoding="utf-8")
        (language_dir / f"{instruction}_work.md").write_text(work, encoding="utf-8")


class BootstrapPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.repository = LocalJsonRepository(self.temp_dir.name)
        self.root_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_missing_files_return_default_persistent_state(self) -> None:
        result = load_bootstrap_data(self.repository)

        self.assertTrue(result.success)
        self.assertEqual(AppSettings(), result.snapshot.settings)
        self.assertEqual((), result.snapshot.saved_workspaces)

    def test_invalid_json_files_report_issue_and_return_default_snapshot(self) -> None:
        (self.root_path / PERSISTENCE_FILE_NAME).write_text("{invalid", encoding="utf-8")

        with self.assertLogs("infra.repository", level="WARNING") as captured_logs:
            result = load_bootstrap_data(self.repository)

        self.assertFalse(result.success)
        self.assertEqual(AppSettings(), result.snapshot.settings)
        self.assertEqual((), result.snapshot.saved_workspaces)
        self.assertEqual(
            ("load_settings", "load_saved_workspaces"),
            tuple(issue.operation for issue in result.issues),
        )
        self.assertEqual(2, len(captured_logs.output))

    def test_partially_broken_sections_block_saves_and_preserve_file(self) -> None:
        data_path = self.root_path / PERSISTENCE_FILE_NAME

        data_path.write_text('{"settings": "broken", "saved_workspaces": []}', encoding="utf-8")
        original_payload = data_path.read_text(encoding="utf-8")

        with self.assertRaises(PersistenceLoadError):
            LocalJsonRepository(self.temp_dir.name).save_saved_workspaces(())

        self.assertEqual(original_payload, data_path.read_text(encoding="utf-8"))

        data_path.write_text('{"settings": {}, "saved_workspaces": "broken"}', encoding="utf-8")
        original_payload = data_path.read_text(encoding="utf-8")

        with self.assertRaises(PersistenceLoadError):
            LocalJsonRepository(self.temp_dir.name).save_settings(AppSettings())

        self.assertEqual(original_payload, data_path.read_text(encoding="utf-8"))

    def test_save_and_reload_round_trip_preserves_persistent_data(self) -> None:
        settings = AppSettings(
            agent_provider="claude_code",
            executable_path=r"C:\Tools\agent.exe",
            executable_paths={
                "codex": r"C:\Tools\codex.exe",
                "opencode": r"C:\Tools\opencode.exe",
            },
            output_font_size=14,
            execution_timeout_minutes=90,
            inactivity_timeout_minutes=15,
            termination_grace_seconds=7,
            file_logging_enabled=False,
            ui_language="en",
            default_model="claude-sonnet",
            default_reasoning_effort="high",
        )
        workspaces = (
            SavedWorkspace(
                path=r"C:\Repo\alpha",
                display_name="Alpha",
                added_at=_dt(9),
                last_selected_at=_dt(10),
            ),
            SavedWorkspace(
                path=r"C:\Repo\beta",
                display_name="Beta",
                added_at=_dt(11),
                last_selected_at=None,
            ),
        )

        self.assertTrue(save_app_settings(self.repository, settings).success)
        self.assertTrue(save_saved_workspaces(self.repository, workspaces).success)

        reloaded = load_bootstrap_data(LocalJsonRepository(self.temp_dir.name))

        self.assertTrue(reloaded.success)
        self.assertEqual(settings, reloaded.snapshot.settings)
        self.assertEqual(workspaces, reloaded.snapshot.saved_workspaces)

        persisted_payload = (self.root_path / PERSISTENCE_FILE_NAME).read_text(encoding="utf-8")
        self.assertIn('"settings"', persisted_payload)
        self.assertIn('"saved_workspaces"', persisted_payload)
        self.assertIn('"agent_provider": "claude_code"', persisted_payload)
        self.assertIn('"executable_paths"', persisted_payload)
        self.assertIn('"codex": "C:\\\\Tools\\\\codex.exe"', persisted_payload)
        self.assertIn('"claude_code": "C:\\\\Tools\\\\agent.exe"', persisted_payload)
        self.assertIn('"opencode": "C:\\\\Tools\\\\opencode.exe"', persisted_payload)
        self.assertIn('"execution_timeout_minutes": 90', persisted_payload)
        self.assertIn('"inactivity_timeout_minutes": 15', persisted_payload)
        self.assertIn('"termination_grace_seconds": 7', persisted_payload)
        self.assertIn('"file_logging_enabled": false', persisted_payload)
        self.assertIn('"ui_language": "en"', persisted_payload)
        self.assertIn('"default_model": "claude-sonnet"', persisted_payload)
        self.assertIn('"default_reasoning_effort": "high"', persisted_payload)

    def test_combined_payload_preserves_provider_executable_paths(self) -> None:
        (self.root_path / PERSISTENCE_FILE_NAME).write_text(
            json.dumps(
                {
                    "settings": {
                        "agent_provider": "opencode",
                        "executable_path": r"C:\Tools\legacy-opencode.exe",
                        "executable_paths": {
                            "codex": r"C:\Tools\codex.exe",
                            "opencode": r"C:\Tools\opencode.exe",
                        },
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        reloaded = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertEqual("opencode", reloaded.agent_provider)
        self.assertEqual(r"C:\Tools\opencode.exe", reloaded.executable_path)
        self.assertEqual(r"C:\Tools\codex.exe", reloaded.executable_paths["codex"])
        self.assertEqual(r"C:\Tools\opencode.exe", reloaded.executable_paths["opencode"])

    def test_combined_payload_migrates_legacy_executable_path_to_current_provider(self) -> None:
        (self.root_path / PERSISTENCE_FILE_NAME).write_text(
            json.dumps(
                {
                    "settings": {
                        "agent_provider": "opencode",
                        "executable_path": r"C:\Tools\opencode.exe",
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        reloaded = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertEqual("opencode", reloaded.agent_provider)
        self.assertEqual(r"C:\Tools\opencode.exe", reloaded.executable_path)
        self.assertEqual(
            {"opencode": r"C:\Tools\opencode.exe"},
            dict(reloaded.executable_paths),
        )

    def test_combined_payload_loads_legacy_progress_logging_key(self) -> None:
        (self.root_path / PERSISTENCE_FILE_NAME).write_text(
            json.dumps(
                {
                    "settings": {
                        "progress_logging_enabled": False,
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        reloaded = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertFalse(reloaded.file_logging_enabled)

    def test_combined_payload_missing_agent_provider_uses_codex_default(self) -> None:
        (self.root_path / PERSISTENCE_FILE_NAME).write_text(
            json.dumps(
                {
                    "settings": {
                        "executable_path": r"C:\Tools\agent.exe",
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        reloaded = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertEqual(DEFAULT_AGENT_PROVIDER, reloaded.agent_provider)

    def test_unknown_agent_provider_falls_back_to_codex_default(self) -> None:
        (self.root_path / PERSISTENCE_FILE_NAME).write_text(
            json.dumps(
                {
                    "settings": {
                        "agent_provider": "unknown_provider",
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        reloaded = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertEqual(DEFAULT_AGENT_PROVIDER, reloaded.agent_provider)

    def test_combined_payload_missing_execution_control_settings_uses_defaults(self) -> None:
        (self.root_path / PERSISTENCE_FILE_NAME).write_text(
            json.dumps(
                {
                    "settings": {
                        "executable_path": r"C:\Tools\agent.exe",
                        "default_model": "gpt-5.4",
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        reloaded = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertEqual(
            AppSettings().execution_timeout_minutes,
            reloaded.execution_timeout_minutes,
        )
        self.assertEqual(
            AppSettings().inactivity_timeout_minutes,
            reloaded.inactivity_timeout_minutes,
        )
        self.assertEqual(
            AppSettings().termination_grace_seconds,
            reloaded.termination_grace_seconds,
        )

    def test_legacy_model_settings_load_and_migrate_on_next_save(self) -> None:
        data_path = self.root_path / PERSISTENCE_FILE_NAME
        data_path.write_text(
            json.dumps(
                {
                    "settings": {
                        "agent_provider": "pi",
                        "default_model": "legacy-model",
                        "model_reasoning_effort": "high",
                        "output_font_size": 15,
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        repository = LocalJsonRepository(self.temp_dir.name)
        reloaded = repository.load_settings()

        self.assertEqual("pi", reloaded.agent_provider)
        self.assertEqual(15, reloaded.output_font_size)
        self.assertEqual(
            AgentExecutionOptions(
                agent_provider="pi",
                model="legacy-model",
                reasoning_effort="high",
            ),
            execution_options_from_settings(reloaded),
        )

        repository.save_settings(reloaded)

        persisted_payload = json.loads(data_path.read_text(encoding="utf-8"))
        persisted_settings = persisted_payload["settings"]
        self.assertEqual("legacy-model", persisted_settings["default_model"])
        self.assertEqual("high", persisted_settings["default_reasoning_effort"])
        self.assertNotIn("model_reasoning_effort", persisted_settings)

    def test_legacy_model_settings_are_migrated_when_saving_workspaces(self) -> None:
        data_path = self.root_path / PERSISTENCE_FILE_NAME
        data_path.write_text(
            json.dumps(
                {
                    "settings": {
                        "agent_provider": "codex",
                        "default_model": "gpt-5.4",
                        "model_reasoning_effort": "high",
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )
        saved_workspace = SavedWorkspace(
            path=r"C:\Repo\alpha",
            display_name="alpha",
            added_at=_dt(9),
            last_selected_at=_dt(10),
        )

        repository = LocalJsonRepository(self.temp_dir.name)
        repository.save_saved_workspaces((saved_workspace,))

        persisted_payload = json.loads(data_path.read_text(encoding="utf-8"))
        persisted_settings = persisted_payload["settings"]
        self.assertEqual("gpt-5.4", persisted_settings["default_model"])
        self.assertEqual("high", persisted_settings["default_reasoning_effort"])
        self.assertNotIn("model_reasoning_effort", persisted_settings)
        self.assertEqual(1, len(persisted_payload["saved_workspaces"]))

    def test_execution_control_settings_preserve_zero_and_reject_negative_values(self) -> None:
        data_path = self.root_path / PERSISTENCE_FILE_NAME
        data_path.write_text(
            json.dumps(
                {
                    "settings": {
                        "execution_timeout_minutes": 0,
                        "inactivity_timeout_minutes": 0,
                        "termination_grace_seconds": 0,
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        disabled = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertEqual(0, disabled.execution_timeout_minutes)
        self.assertEqual(0, disabled.inactivity_timeout_minutes)
        self.assertEqual(0, disabled.termination_grace_seconds)

        data_path.write_text(
            json.dumps(
                {
                    "settings": {
                        "execution_timeout_minutes": -1,
                        "inactivity_timeout_minutes": -2,
                        "termination_grace_seconds": -3,
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        reloaded = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertEqual(
            AppSettings().execution_timeout_minutes,
            reloaded.execution_timeout_minutes,
        )
        self.assertEqual(
            AppSettings().inactivity_timeout_minutes,
            reloaded.inactivity_timeout_minutes,
        )
        self.assertEqual(
            AppSettings().termination_grace_seconds,
            reloaded.termination_grace_seconds,
        )

    def test_save_settings_rejects_invalid_execution_control_values_at_boundary(self) -> None:
        data_path = self.root_path / PERSISTENCE_FILE_NAME
        self.repository.save_settings(AppSettings())
        original_payload = data_path.read_text(encoding="utf-8")

        invalid_cases = (
            ("execution_timeout_minutes", -1),
            ("inactivity_timeout_minutes", "abc"),
            ("termination_grace_seconds", True),
        )

        for field_name, invalid_value in invalid_cases:
            with self.subTest(field_name=field_name, invalid_value=invalid_value):
                corrupted_settings = _unsafe_settings_with_value(
                    field_name,
                    invalid_value,
                )

                with self.assertRaises(PersistenceSaveError):
                    self.repository.save_settings(corrupted_settings)

                save_result = save_app_settings(self.repository, corrupted_settings)

                self.assertFalse(save_result.success)
                self.assertIsNotNone(save_result.issue)
                self.assertEqual("save_settings", save_result.issue.operation)
                self.assertEqual(original_payload, data_path.read_text(encoding="utf-8"))

    def test_legacy_split_files_are_loaded_as_fallback(self) -> None:
        (self.root_path / "settings.json").write_text(
            (
                "{\n"
                '  "executable_path": "C:\\\\Tools\\\\agent.exe",\n'
                '  "output_font_size": 13,\n'
                '  "default_model": "gpt-5.4-mini",\n'
                '  "model_reasoning_effort": "medium"\n'
                "}\n"
            ),
            encoding="utf-8",
        )
        (self.root_path / "saved_workspaces.json").write_text(
            (
                "[\n"
                "  {\n"
                '    "path": "C:\\\\Repo\\\\alpha",\n'
                '    "display_name": "Alpha",\n'
                '    "added_at": "2026-04-22T09:00:00+00:00",\n'
                '    "last_selected_at": "2026-04-22T10:00:00+00:00"\n'
                "  }\n"
                "]\n"
            ),
            encoding="utf-8",
        )

        result = load_bootstrap_data(self.repository)

        self.assertTrue(result.success)
        self.assertEqual(
            AppSettings(
                executable_path=r"C:\Tools\agent.exe",
                output_font_size=13,
                agent_provider=DEFAULT_AGENT_PROVIDER,
                default_model="gpt-5.4-mini",
                default_reasoning_effort="medium",
            ),
            result.snapshot.settings,
        )
        self.assertEqual(1, len(result.snapshot.saved_workspaces))
        self.assertEqual(r"C:\Repo\alpha", result.snapshot.saved_workspaces[0].path)

    def test_unsafe_output_font_sizes_fall_back_to_default(self) -> None:
        data_path = self.root_path / PERSISTENCE_FILE_NAME

        for unsafe_font_size in (0, -4, 73, 10_000):
            with self.subTest(unsafe_font_size=unsafe_font_size):
                data_path.write_text(
                    json.dumps({"settings": {"output_font_size": unsafe_font_size}}),
                    encoding="utf-8",
                )

                reloaded = LocalJsonRepository(self.temp_dir.name).load_settings()

                self.assertEqual(AppSettings().output_font_size, reloaded.output_font_size)

    def test_unsupported_ui_language_falls_back_to_default(self) -> None:
        data_path = self.root_path / PERSISTENCE_FILE_NAME
        data_path.write_text(
            json.dumps({"settings": {"ui_language": "fr"}, "saved_workspaces": []}),
            encoding="utf-8",
        )

        reloaded = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertEqual(AppSettings().ui_language, reloaded.ui_language)


class PersistenceUseCaseErrorTests(unittest.TestCase):
    def test_load_use_case_returns_user_facing_issue_when_repository_raises(self) -> None:
        repository = _FailingRepository(load_error=PersistenceLoadError("boom", path=Path("settings.json"), operation="load"))

        result = load_bootstrap_data(repository)

        self.assertFalse(result.success)
        self.assertEqual(AppSettings(), result.snapshot.settings)
        self.assertEqual((), result.snapshot.saved_workspaces)
        self.assertEqual(
            ("load_settings", "load_saved_workspaces"),
            tuple(issue.operation for issue in result.issues),
        )

    def test_save_use_case_returns_user_facing_issue_when_repository_raises(self) -> None:
        repository = _FailingRepository(save_error=PersistenceSaveError("boom", path=Path("settings.json"), operation="save"))

        settings_result = save_app_settings(repository, AppSettings())
        workspaces_result = save_saved_workspaces(repository, ())

        self.assertFalse(settings_result.success)
        self.assertEqual("설정을 저장하지 못했습니다.", settings_result.issue.message)
        self.assertFalse(workspaces_result.success)
        self.assertEqual(
            "워크스페이스 목록을 저장하지 못했습니다.",
            workspaces_result.issue.message,
        )


class _FailingRepository:
    def __init__(
        self,
        *,
        load_error: Exception | None = None,
        save_error: Exception | None = None,
    ) -> None:
        self._load_error = load_error
        self._save_error = save_error

    def load_settings(self) -> AppSettings:
        if self._load_error is not None:
            raise self._load_error
        return AppSettings()

    def save_settings(self, settings: AppSettings) -> None:
        if self._save_error is not None:
            raise self._save_error

    def load_saved_workspaces(self) -> tuple[SavedWorkspace, ...]:
        if self._load_error is not None:
            raise self._load_error
        return ()

    def save_saved_workspaces(self, workspaces: tuple[SavedWorkspace, ...]) -> None:
        if self._save_error is not None:
            raise self._save_error


