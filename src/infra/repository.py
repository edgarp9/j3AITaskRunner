"""Local JSON persistence and prompt asset lookup for j3AITaskRunner."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, NoReturn, Sequence

from domain import (
    AppSettings,
    InstructionInfo,
    SavedWorkspace,
    SUPPORTED_AGENT_PROVIDERS,
    normalize_agent_executable_paths,
    normalize_agent_provider,
    normalize_ui_language,
    workspace_folder_display_name,
)
from domain.models import EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX, TERMINATION_GRACE_SECONDS_MAX

LOGGER = logging.getLogger(__name__)

PERSISTENCE_FILE_NAME = "j3AITaskRunner.json"
LEGACY_SETTINGS_FILE_NAME = "settings.json"
LEGACY_WORKSPACES_FILE_NAME = "saved_workspaces.json"
DEFAULT_SETTINGS = AppSettings()
OUTPUT_FONT_SIZE_MIN = 1
OUTPUT_FONT_SIZE_MAX = 72
_CACHE_UNSET = object()
_CANDIDATES_PAYLOAD_PATTERN = re.compile(r"\{\{\s*candidates_payload\s*\}\}")
_UNSAFE_PROMPT_SEGMENT_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_PROMPT_SEGMENT_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


class PersistenceError(Exception):
    """Base error for persistence failures that should reach the app layer."""

    def __init__(self, message: str, *, path: Path, operation: str) -> None:
        super().__init__(message)
        self.path = path
        self.operation = operation


class PersistenceLoadError(PersistenceError):
    """Raised when a persistent file cannot be read safely."""


class PersistenceSaveError(PersistenceError):
    """Raised when a persistent file cannot be written safely."""


@dataclass(slots=True, frozen=True)
class StoragePaths:
    """Resolved file locations for persistent data."""

    root_dir: Path
    data_path: Path
    legacy_settings_path: Path
    legacy_workspaces_path: Path


class PromptStoreError(Exception):
    """Raised when prompt assets cannot be enumerated or read."""


class PromptStore:
    """Read j3aiPromptLoop-compatible prompt assets from the app bundle."""

    def __init__(self, app_base_dir: str | Path) -> None:
        self._app_base_dir = Path(app_base_dir)
        self._prompt_root = self._resolve_prompt_root()
        self._cache_lock = threading.RLock()
        self._language_cache: tuple[str, ...] | None = None
        self._instruction_list_cache: dict[str, tuple[InstructionInfo, ...]] = {}
        self._instruction_info_cache: dict[tuple[str, str], InstructionInfo] = {}
        self._instruction_pair_cache: dict[tuple[str, str], bool] = {}
        self._prompt_text_cache: dict[Path, str] = {}

    @property
    def prompt_root(self) -> Path:
        """Return the selected prompt root directory."""
        return self._prompt_root

    def list_languages(self) -> list[str]:
        """Return available language directories in display order."""
        with self._cache_lock:
            if self._language_cache is not None:
                return list(self._language_cache)

        try:
            if not self._prompt_root.is_dir():
                languages: tuple[str, ...] = ()
            else:
                language_names: list[str] = []
                for entry in sorted(
                    self._prompt_root.iterdir(),
                    key=lambda item: item.name.casefold(),
                ):
                    if not entry.is_dir():
                        continue
                    if entry.name.startswith("_"):
                        continue
                    if not self.is_safe_segment(entry.name):
                        continue
                    language_names.append(entry.name)
                languages = tuple(language_names)
        except OSError as exc:
            LOGGER.exception(
                "Failed to enumerate prompt languages. prompt_root=%s",
                self._prompt_root,
            )
            raise PromptStoreError("Failed to enumerate prompt languages.") from exc
        with self._cache_lock:
            self._language_cache = languages
        return list(languages)

    def list_instructions(self, language: str) -> list[InstructionInfo]:
        """Return instructions that have both analysis and work prompt files."""
        safe_language = self._validate_segment(language, field_name="language")
        with self._cache_lock:
            cached_instructions = self._instruction_list_cache.get(safe_language)
            if cached_instructions is not None:
                return list(cached_instructions)

        language_dir = self._prompt_root / safe_language
        try:
            if not language_dir.is_dir():
                instructions: tuple[InstructionInfo, ...] = ()
            else:
                instruction_infos: list[InstructionInfo] = []
                for path in sorted(
                    language_dir.glob("*.md"),
                    key=lambda item: item.name.casefold(),
                ):
                    if path.name.endswith("_work.md"):
                        continue
                    instruction = path.stem
                    if not self.is_safe_segment(instruction):
                        continue
                    if not self.has_instruction_pair(safe_language, instruction):
                        continue
                    instruction_infos.append(
                        self._build_instruction_info(safe_language, instruction)
                    )
                instructions = tuple(instruction_infos)
        except OSError as exc:
            LOGGER.exception(
                "Failed to enumerate prompt instructions. language_dir=%s",
                language_dir,
            )
            raise PromptStoreError("Failed to enumerate prompt instructions.") from exc
        with self._cache_lock:
            self._instruction_list_cache[safe_language] = instructions
            for info in instructions:
                key = (info.language, info.instruction)
                self._instruction_info_cache[key] = info
                self._instruction_pair_cache[key] = True
        return list(instructions)

    def get_instruction(self, language: str, instruction: str) -> InstructionInfo | None:
        """Return one instruction when its analysis/work prompt pair exists."""
        safe_language = self._validate_segment(language, field_name="language")
        safe_instruction = self._validate_segment(instruction, field_name="instruction")
        cache_key = (safe_language, safe_instruction)
        with self._cache_lock:
            cached_info = self._instruction_info_cache.get(cache_key)
            if cached_info is not None:
                return cached_info

        if not self.has_instruction_pair(safe_language, safe_instruction):
            return None
        info = self._build_instruction_info(safe_language, safe_instruction)
        with self._cache_lock:
            self._instruction_info_cache[cache_key] = info
        return info

    def has_instruction_pair(self, language: str, instruction: str) -> bool:
        """Return whether ``instruction.md`` and ``instruction_work.md`` both exist."""
        safe_language = self._validate_segment(language, field_name="language")
        safe_instruction = self._validate_segment(instruction, field_name="instruction")
        cache_key = (safe_language, safe_instruction)
        with self._cache_lock:
            cached_result = self._instruction_pair_cache.get(cache_key)
            if cached_result is not None:
                return cached_result

        analysis_path, work_path = self._instruction_paths(safe_language, safe_instruction)
        try:
            has_pair = analysis_path.is_file() and work_path.is_file()
        except OSError as exc:
            LOGGER.exception(
                "Failed to inspect prompt instruction pair. language=%s instruction=%s",
                safe_language,
                safe_instruction,
            )
            raise PromptStoreError("Failed to inspect prompt instruction pair.") from exc
        with self._cache_lock:
            self._instruction_pair_cache[cache_key] = has_pair
        return has_pair

    def read_analysis_prompt(self, language: str, instruction: str) -> str:
        """Read the analysis prompt text for one instruction."""
        info = self._get_required_instruction(language, instruction)
        return self._read_prompt_file(Path(info.analysis_prompt_path))

    def read_work_prompt_template(self, language: str, instruction: str) -> str:
        """Read the work prompt template text for one instruction."""
        info = self._get_required_instruction(language, instruction)
        return self._read_prompt_file(Path(info.work_prompt_template_path))

    def render_work_prompt(
        self,
        language: str,
        instruction: str,
        *,
        candidates_payload: str,
    ) -> str:
        """Render a work prompt by replacing ``{{candidates_payload}}``."""
        template_text = self.read_work_prompt_template(language, instruction)
        return self.render_template_text(
            template_text,
            candidates_payload=candidates_payload,
        )

    def render_template_text(self, template_text: str, *, candidates_payload: str) -> str:
        """Replace supported prompt template placeholders in text."""
        if _CANDIDATES_PAYLOAD_PATTERN.search(template_text) is None:
            raise PromptStoreError(
                "Prompt template is missing the candidates_payload placeholder."
            )
        return _CANDIDATES_PAYLOAD_PATTERN.sub(
            lambda _match: candidates_payload,
            template_text,
        )

    def is_safe_segment(self, value: str) -> bool:
        """Return whether a language or instruction name is one filesystem segment."""
        if not isinstance(value, str):
            return False
        if not value or value in {".", ".."}:
            return False
        if value != value.strip():
            return False
        if "/" in value or "\\" in value:
            return False
        if any(
            character in _UNSAFE_PROMPT_SEGMENT_CHARACTERS or ord(character) < 32
            for character in value
        ):
            return False
        if value.endswith((".", " ")):
            return False
        if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_PROMPT_SEGMENT_NAMES:
            return False
        path_value = Path(value)
        windows_path_value = PureWindowsPath(value)
        if path_value.is_absolute() or windows_path_value.is_absolute():
            return False
        if windows_path_value.drive:
            return False
        if ".." in path_value.parts or ".." in windows_path_value.parts:
            return False
        return True

    def _resolve_prompt_root(self) -> Path:
        candidates = [
            self._app_base_dir / "prompt",
            self._app_base_dir / "lib" / "prompt",
        ]
        bundled_root = getattr(sys, "_MEIPASS", None)
        if bundled_root:
            bundled_base_dir = Path(bundled_root)
            candidates.extend(
                [
                    bundled_base_dir / "prompt",
                    bundled_base_dir / "lib" / "prompt",
                ]
            )

        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return self._app_base_dir / "lib" / "prompt"

    def _get_required_instruction(self, language: str, instruction: str) -> InstructionInfo:
        info = self.get_instruction(language, instruction)
        if info is None:
            raise PromptStoreError(
                f"Prompt instruction pair is missing: {language}/{instruction}"
            )
        return info

    def _build_instruction_info(self, language: str, instruction: str) -> InstructionInfo:
        analysis_path, work_path = self._instruction_paths(language, instruction)
        return InstructionInfo(
            language=language,
            instruction=instruction,
            analysis_prompt_path=str(analysis_path),
            work_prompt_template_path=str(work_path),
        )

    def _instruction_paths(self, language: str, instruction: str) -> tuple[Path, Path]:
        language_dir = self._prompt_root / language
        return (
            language_dir / f"{instruction}.md",
            language_dir / f"{instruction}_work.md",
        )

    def _read_prompt_file(self, path: Path) -> str:
        with self._cache_lock:
            cached_text = self._prompt_text_cache.get(path)
            if cached_text is not None:
                return cached_text

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            LOGGER.exception("Failed to read prompt file. path=%s", path)
            raise PromptStoreError("Failed to read prompt file.") from exc
        with self._cache_lock:
            self._prompt_text_cache[path] = text
        return text

    def _validate_segment(self, value: str, *, field_name: str) -> str:
        if not self.is_safe_segment(value):
            raise ValueError(f"Unsafe {field_name} name: {value!r}")
        return value


class LocalJsonRepository:
    """Store persistent settings and saved workspaces in one local JSON file."""

    def __init__(self, root_dir: str | Path) -> None:
        storage_root = Path(root_dir)
        self.paths = StoragePaths(
            root_dir=storage_root,
            data_path=storage_root / PERSISTENCE_FILE_NAME,
            legacy_settings_path=storage_root / LEGACY_SETTINGS_FILE_NAME,
            legacy_workspaces_path=storage_root / LEGACY_WORKSPACES_FILE_NAME,
        )
        self._data_payload_cache: dict[str, Any] | None | object = _CACHE_UNSET
        self._legacy_settings_cache: dict[str, Any] | None | object = _CACHE_UNSET
        self._legacy_workspaces_cache: list[Any] | None | object = _CACHE_UNSET

    def load_settings(self) -> AppSettings:
        """Load persistent application settings or return defaults."""
        payload = self._load_combined_payload()
        if payload is None:
            return self._load_legacy_settings()

        if "settings" not in payload:
            self._raise_invalid_load(
                "Persistent data file is missing the settings section.",
                path=self.paths.data_path,
            )

        settings_payload = payload["settings"]
        if not isinstance(settings_payload, dict):
            self._raise_invalid_load(
                "Settings section is not an object.",
                path=self.paths.data_path,
            )

        return self._deserialize_settings(settings_payload)

    def save_settings(self, settings: AppSettings) -> None:
        """Persist application settings to disk."""
        workspaces = self.load_saved_workspaces()
        payload = self._build_persistence_payload(
            settings=settings,
            workspaces=workspaces,
        )
        self._save_json_file(self.paths.data_path, payload)
        self._data_payload_cache = payload

    def load_saved_workspaces(self) -> tuple[SavedWorkspace, ...]:
        """Load saved workspaces or return an empty list when no file exists yet."""
        payload = self._load_combined_payload()
        if payload is None:
            return self._load_legacy_saved_workspaces()

        if "saved_workspaces" not in payload:
            self._raise_invalid_load(
                "Persistent data file is missing the saved_workspaces section.",
                path=self.paths.data_path,
            )

        workspaces_payload = payload["saved_workspaces"]
        if not isinstance(workspaces_payload, list):
            self._raise_invalid_load(
                "Saved workspaces section is not a list.",
                path=self.paths.data_path,
            )

        return self._deserialize_saved_workspaces(
            workspaces_payload,
            source_path=self.paths.data_path,
        )

    def save_saved_workspaces(self, workspaces: Sequence[SavedWorkspace]) -> None:
        """Persist saved workspaces to disk."""
        settings = self.load_settings()
        payload = self._build_persistence_payload(
            settings=settings,
            workspaces=workspaces,
        )
        self._save_json_file(self.paths.data_path, payload)
        self._data_payload_cache = payload

    def _load_combined_payload(self) -> dict[str, Any] | None:
        if self._data_payload_cache is not _CACHE_UNSET:
            return self._data_payload_cache

        payload = self._load_json_file(
            path=self.paths.data_path,
            empty_message="Persistent data file is missing. Using defaults.",
            corrupt_message="Persistent data file is invalid.",
        )
        if payload is None:
            self._data_payload_cache = None
            return None

        if not isinstance(payload, dict):
            self._raise_invalid_load(
                "Persistent data payload is not an object.",
                path=self.paths.data_path,
            )

        self._data_payload_cache = payload
        return payload

    def _load_legacy_settings(self) -> AppSettings:
        payload = self._load_legacy_settings_payload()
        if payload is None:
            return DEFAULT_SETTINGS
        return self._deserialize_settings(payload)

    def _load_legacy_settings_payload(self) -> dict[str, Any] | None:
        if self._legacy_settings_cache is not _CACHE_UNSET:
            return self._legacy_settings_cache

        payload = self._load_json_file(
            path=self.paths.legacy_settings_path,
            empty_message="Legacy settings file is missing. Using defaults.",
            corrupt_message="Legacy settings file is invalid.",
        )
        if payload is None:
            self._legacy_settings_cache = None
            return None

        if not isinstance(payload, dict):
            self._raise_invalid_load(
                "Legacy settings payload is not an object.",
                path=self.paths.legacy_settings_path,
            )

        self._legacy_settings_cache = payload
        return payload

    def _load_legacy_saved_workspaces(self) -> tuple[SavedWorkspace, ...]:
        payload = self._load_legacy_saved_workspaces_payload()
        if payload is None:
            return ()
        return self._deserialize_saved_workspaces(
            payload,
            source_path=self.paths.legacy_workspaces_path,
        )

    def _load_legacy_saved_workspaces_payload(self) -> list[Any] | None:
        if self._legacy_workspaces_cache is not _CACHE_UNSET:
            return self._legacy_workspaces_cache

        payload = self._load_json_file(
            path=self.paths.legacy_workspaces_path,
            empty_message="Legacy saved workspaces file is missing. Using empty list.",
            corrupt_message="Legacy saved workspaces file is invalid.",
        )
        if payload is None:
            self._legacy_workspaces_cache = None
            return None

        if not isinstance(payload, list):
            self._raise_invalid_load(
                "Legacy saved workspaces payload is not a list.",
                path=self.paths.legacy_workspaces_path,
            )

        self._legacy_workspaces_cache = payload
        return payload

    def _build_persistence_payload(
        self,
        *,
        settings: AppSettings,
        workspaces: Sequence[SavedWorkspace],
    ) -> dict[str, Any]:
        return {
            "settings": self._serialize_settings(settings),
            "saved_workspaces": self._serialize_saved_workspaces(workspaces),
        }

    def _serialize_settings(self, settings: AppSettings) -> dict[str, Any]:
        agent_provider = normalize_agent_provider(
            getattr(settings, "agent_provider", DEFAULT_SETTINGS.agent_provider)
        )
        executable_path = self._require_optional_string(
            getattr(settings, "executable_path", None),
            field_name="executable_path",
        )
        executable_paths = self._serialize_executable_paths(
            settings,
            agent_provider=agent_provider,
            executable_path=executable_path,
        )
        return {
            "agent_provider": agent_provider,
            "executable_path": executable_path,
            "executable_paths": executable_paths,
            "output_font_size": settings.output_font_size,
            "execution_timeout_minutes": self._require_non_negative_int(
                settings.execution_timeout_minutes,
                field_name="execution_timeout_minutes",
            ),
            "inactivity_timeout_minutes": self._require_non_negative_int(
                settings.inactivity_timeout_minutes,
                field_name="inactivity_timeout_minutes",
            ),
            "termination_grace_seconds": self._require_non_negative_int(
                settings.termination_grace_seconds,
                field_name="termination_grace_seconds",
            ),
            "file_logging_enabled": settings.file_logging_enabled,
            "ui_language": normalize_ui_language(settings.ui_language),
            "default_model": self._require_string(
                getattr(settings, "default_model", DEFAULT_SETTINGS.default_model),
                field_name="default_model",
            ),
            "default_reasoning_effort": self._require_string(
                getattr(
                    settings,
                    "default_reasoning_effort",
                    DEFAULT_SETTINGS.default_reasoning_effort,
                ),
                field_name="default_reasoning_effort",
            ),
        }

    def _deserialize_settings(self, payload: dict[str, Any]) -> AppSettings:
        agent_provider = normalize_agent_provider(payload.get("agent_provider"))
        executable_paths = normalize_agent_executable_paths(
            payload.get("executable_paths")
        )
        legacy_executable_path = self._coerce_optional_string(
            payload.get("executable_path")
        )
        if legacy_executable_path is not None and agent_provider not in executable_paths:
            executable_paths[agent_provider] = legacy_executable_path

        return AppSettings(
            executable_path=executable_paths.get(agent_provider),
            executable_paths=executable_paths,
            output_font_size=self._coerce_int_in_range(
                payload.get("output_font_size"),
                DEFAULT_SETTINGS.output_font_size,
                min_value=OUTPUT_FONT_SIZE_MIN,
                max_value=OUTPUT_FONT_SIZE_MAX,
            ),
            execution_timeout_minutes=self._coerce_int_in_range(
                payload.get("execution_timeout_minutes"),
                DEFAULT_SETTINGS.execution_timeout_minutes,
                min_value=0,
                max_value=EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
            ),
            inactivity_timeout_minutes=self._coerce_int_in_range(
                payload.get("inactivity_timeout_minutes"),
                DEFAULT_SETTINGS.inactivity_timeout_minutes,
                min_value=0,
                max_value=EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
            ),
            termination_grace_seconds=self._coerce_int_in_range(
                payload.get("termination_grace_seconds"),
                DEFAULT_SETTINGS.termination_grace_seconds,
                min_value=0,
                max_value=TERMINATION_GRACE_SECONDS_MAX,
            ),
            file_logging_enabled=self._coerce_bool(
                payload.get(
                    "file_logging_enabled",
                    payload.get("progress_logging_enabled"),
                ),
                DEFAULT_SETTINGS.file_logging_enabled,
            ),
            ui_language=normalize_ui_language(
                self._coerce_string(
                    payload.get("ui_language"),
                    DEFAULT_SETTINGS.ui_language,
                )
            ),
            agent_provider=agent_provider,
            default_model=self._coerce_string(
                payload.get("default_model"),
                DEFAULT_SETTINGS.default_model,
            ),
            default_reasoning_effort=self._coerce_string(
                payload.get(
                    "default_reasoning_effort",
                    payload.get("model_reasoning_effort"),
                ),
                DEFAULT_SETTINGS.default_reasoning_effort,
            ),
        )

    def _serialize_executable_paths(
        self,
        settings: AppSettings,
        *,
        agent_provider: str,
        executable_path: str | None,
    ) -> dict[str, str]:
        executable_paths = normalize_agent_executable_paths(
            getattr(settings, "executable_paths", {})
        )
        if executable_path is not None:
            executable_paths[agent_provider] = executable_path

        return {
            provider: executable_paths[provider]
            for provider in SUPPORTED_AGENT_PROVIDERS
            if provider in executable_paths
        }

    def _serialize_saved_workspaces(self, workspaces: Sequence[SavedWorkspace]) -> list[dict[str, Any]]:
        return [
            {
                "path": workspace.path,
                "display_name": workspace.display_name,
                "added_at": self._serialize_datetime(workspace.added_at),
                "last_selected_at": self._serialize_datetime(workspace.last_selected_at),
            }
            for workspace in workspaces
        ]

    def _deserialize_saved_workspaces(
        self,
        payload: list[Any],
        *,
        source_path: Path,
    ) -> tuple[SavedWorkspace, ...]:
        workspaces: list[SavedWorkspace] = []
        for index, item in enumerate(payload):
            workspace = self._deserialize_saved_workspace(
                item=item,
                index=index,
                source_path=source_path,
            )
            if workspace is not None:
                workspaces.append(workspace)

        return tuple(workspaces)

    def _load_json_file(
        self,
        path: Path,
        *,
        empty_message: str,
        corrupt_message: str,
    ) -> Any | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            LOGGER.info("%s path=%s", empty_message, path)
            return None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOGGER.warning("%s path=%s", corrupt_message, path, exc_info=exc)
            raise PersistenceLoadError(
                corrupt_message,
                path=path,
                operation="load",
            ) from exc
        except OSError as exc:
            LOGGER.exception("Failed to load persistent file. path=%s", path)
            raise PersistenceLoadError(
                "Failed to load persistent file.",
                path=path,
                operation="load",
            ) from exc

    def _raise_invalid_load(self, message: str, *, path: Path) -> NoReturn:
        LOGGER.warning("%s path=%s", message, path)
        raise PersistenceLoadError(message, path=path, operation="load")

    def _raise_invalid_save(self, message: str, *, path: Path) -> NoReturn:
        LOGGER.warning("%s path=%s", message, path)
        raise PersistenceSaveError(message, path=path, operation="save")

    def _require_non_negative_int(self, value: Any, *, field_name: str) -> int:
        if type(value) is not int or value < 0:
            self._raise_invalid_save(
                f"Settings field {field_name} must be a non-negative integer.",
                path=self.paths.data_path,
            )
        return value

    def _require_optional_string(self, value: Any, *, field_name: str) -> str | None:
        if value is not None and not isinstance(value, str):
            self._raise_invalid_save(
                f"Settings field {field_name} must be a string or null.",
                path=self.paths.data_path,
            )
        return value

    def _require_string(self, value: Any, *, field_name: str) -> str:
        if not isinstance(value, str):
            self._raise_invalid_save(
                f"Settings field {field_name} must be a string.",
                path=self.paths.data_path,
            )
        return value

    def _save_json_file(self, path: Path, payload: Any) -> None:
        temp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.stem}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temp_path, path)
        except OSError as exc:
            LOGGER.exception("Failed to save persistent file. path=%s", path)
            raise PersistenceSaveError(
                "Failed to save persistent file.",
                path=path,
                operation="save",
            ) from exc
        finally:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    LOGGER.warning(
                        "Temporary persistence file could not be removed. path=%s",
                        temp_path,
                    )

    def _deserialize_saved_workspace(
        self,
        *,
        item: Any,
        index: int,
        source_path: Path,
    ) -> SavedWorkspace | None:
        if not isinstance(item, dict):
            LOGGER.warning(
                "Skipping saved workspace entry because it is not an object. path=%s index=%s",
                source_path,
                index,
            )
            return None

        path = self._coerce_string(item.get("path"), "")
        if not path:
            LOGGER.warning(
                "Skipping saved workspace entry because path is missing. path=%s index=%s",
                source_path,
                index,
            )
            return None

        display_name = self._coerce_string(
            item.get("display_name"),
            workspace_folder_display_name(path),
        )

        try:
            added_at = self._parse_datetime(item.get("added_at"))
            last_selected_at = self._parse_optional_datetime(item.get("last_selected_at"))
        except ValueError as exc:
            LOGGER.warning(
                "Skipping saved workspace entry because timestamps are invalid. path=%s index=%s",
                source_path,
                index,
                exc_info=exc,
            )
            return None

        return SavedWorkspace(
            path=path,
            display_name=display_name,
            added_at=added_at,
            last_selected_at=last_selected_at,
        )

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError("timestamp must be a non-empty string")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        try:
            parsed.timestamp()
        except (OSError, OverflowError, ValueError) as exc:
            raise ValueError("timestamp is outside supported range") from exc
        return parsed

    def _parse_optional_datetime(self, value: Any) -> datetime | None:
        if value is None:
            return None
        return self._parse_datetime(value)

    @staticmethod
    def _coerce_string(value: Any, default: str) -> str:
        return value if isinstance(value, str) else default

    @staticmethod
    def _coerce_optional_string(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        return value if isinstance(value, int) else default

    @staticmethod
    def _coerce_int_in_range(value: Any, default: int, *, min_value: int, max_value: int) -> int:
        coerced = LocalJsonRepository._coerce_int(value, default)
        if coerced < min_value or coerced > max_value:
            return default
        return coerced

    @staticmethod
    def _coerce_non_negative_int(value: Any, default: int) -> int:
        coerced = LocalJsonRepository._coerce_int(value, default)
        if coerced < 0:
            return default
        return coerced

    @staticmethod
    def _coerce_bool(value: Any, default: bool) -> bool:
        return value if isinstance(value, bool) else default
