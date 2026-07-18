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
    normalize_queue_mode,
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

