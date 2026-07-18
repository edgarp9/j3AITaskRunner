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
        "file_logging_enabled": False,
        "ui_language": "en",
        "agent_provider": DEFAULT_AGENT_PROVIDER,
        "default_model": "",
        "default_reasoning_effort": "",
    }.items():
        object.__setattr__(settings, safe_field_name, safe_value)
    object.__setattr__(settings, field_name, value)
    return settings


class BootstrapPersistenceSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.repository = LocalJsonRepository(self.temp_dir.name)
        self.root_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

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
        self.assertEqual(AppSettings().queue_mode, reloaded.queue_mode)

    def test_queue_mode_setting_defaults_and_normalizes_unknown_values(self) -> None:
        data_path = self.root_path / PERSISTENCE_FILE_NAME
        data_path.write_text(
            json.dumps(
                {
                    "settings": {
                        "queue_mode": "shared",
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        shared = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertEqual("shared", shared.queue_mode)

        data_path.write_text(
            json.dumps(
                {
                    "settings": {
                        "queue_mode": "unknown",
                    },
                    "saved_workspaces": [],
                }
            ),
            encoding="utf-8",
        )

        reloaded = LocalJsonRepository(self.temp_dir.name).load_settings()

        self.assertEqual(AppSettings().queue_mode, reloaded.queue_mode)

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

