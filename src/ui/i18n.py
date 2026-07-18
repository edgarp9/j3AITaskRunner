"""Small localization facade used by Tkinter views."""

from __future__ import annotations

from domain.localization import DEFAULT_UI_LANGUAGE, normalize_ui_language

from .i18n_catalog import (
    RUNTIME_EXACT_LINES,
    RUNTIME_MESSAGE_KEYS,
    RUNTIME_PREFIXES,
    RUNTIME_TRANSLATIONS,
    TRANSLATIONS,
    LanguageOption,
)

SOURCE_RUNTIME_LANGUAGE = "ko"


def text(key: str, language: str | None = None, **values: object) -> str:
    """Return translated UI text for ``key``."""
    language = normalize_ui_language(language)
    template = TRANSLATIONS.get(language, {}).get(key)
    if template is None:
        template = TRANSLATIONS[DEFAULT_UI_LANGUAGE].get(key, key)
    if values:
        return template.format(**values)
    return template


def language_options(language: str | None = None) -> tuple[LanguageOption, ...]:
    """Return UI language choices using the current display language."""
    return (
        LanguageOption(label=text("language_en", language), value="en"),
        LanguageOption(label=text("language_ko", language), value="ko"),
    )


def language_label(value: str | None, language: str | None = None) -> str:
    """Return the display label for a stored UI language code."""
    normalized = normalize_ui_language(value)
    for option in language_options(language):
        if option.value == normalized:
            return option.label
    return normalized


def localize_runtime_message(message: str | None, language: str | None = None) -> str:
    """Translate common app-layer Korean runtime messages for display."""
    if not message:
        return ""
    language = normalize_ui_language(language)
    if language == SOURCE_RUNTIME_LANGUAGE:
        return message

    stripped = message.strip()
    exact_key = RUNTIME_MESSAGE_KEYS.get(stripped)
    if exact_key is not None:
        return (
            RUNTIME_TRANSLATIONS.get(language, {}).get(exact_key)
            or TRANSLATIONS.get(language, {}).get(exact_key)
            or stripped
        )

    pattern_text = _localize_runtime_pattern(stripped, language)
    if pattern_text is not None:
        return pattern_text

    for prefix, (_key, translated_prefix) in RUNTIME_PREFIXES.items():
        if stripped.startswith(prefix):
            suffix = stripped[len(prefix) :].strip()
            localized_suffix = localize_runtime_message(suffix, language)
            if localized_suffix:
                return f"{translated_prefix}{localized_suffix}"
            return translated_prefix.rstrip()

    return message


def _localize_runtime_pattern(message: str, language: str) -> str | None:
    if language != "en":
        return None

    if message.startswith("우선순위는 ") and message.endswith(" 중 하나여야 합니다."):
        values = message[len("우선순위는 ") : -len(" 중 하나여야 합니다.")]
        return f"Priority must be one of {values}."

    priority_prefix = "분석 응답 candidate priority는 "
    if message.startswith(priority_prefix) and message.endswith(
        " 중 하나여야 합니다."
    ):
        values = message[len(priority_prefix) : -len(" 중 하나여야 합니다.")]
        return f"Analysis response candidate priority must be one of {values}."

    if message.startswith("시간 제한 초과: 전체 실행 제한"):
        suffix = _localize_timeout_suffix(
            message[len("시간 제한 초과: 전체 실행 제한") :]
        )
        return f"Timeout: execution limit{suffix}"

    if message.startswith("시간 제한 초과: 출력 무활동 제한"):
        suffix = _localize_timeout_suffix(
            message[len("시간 제한 초과: 출력 무활동 제한") :]
        )
        return f"Timeout: output inactivity limit{suffix}"

    abnormal_exit_marker = "가 비정상 종료했습니다. exit_code="
    if abnormal_exit_marker in message:
        name, exit_code = message.split(abnormal_exit_marker, 1)
        return f"{name} exited abnormally. exit_code={exit_code}"

    launch_failed_marker = " 프로세스를 시작하지 못했습니다: "
    if launch_failed_marker in message:
        name, detail = message.split(launch_failed_marker, 1)
        return f"Could not start the {name} process: {detail}"

    if message.endswith(" result error 이벤트를 확인했습니다."):
        name = message[: -len(" result error 이벤트를 확인했습니다.")]
        return f"{name} result error event was received."

    if message.endswith(" turn.failed 이벤트를 확인했습니다."):
        name = message[: -len(" turn.failed 이벤트를 확인했습니다.")]
        return f"{name} turn.failed event was received."

    if message.endswith(" error 이벤트를 확인했습니다."):
        name = message[: -len(" error 이벤트를 확인했습니다.")]
        return f"{name} error event was received."

    log_storage_prefix = (
        "실행 로그을 준비하지 못했습니다. 권한, 용량, 경로를 확인하세요. 원인: "
    )
    if message.startswith(log_storage_prefix):
        detail = message[len(log_storage_prefix) :]
        return (
            "Could not prepare execution logs. "
            f"Check permissions, disk space, and paths. Cause: {detail}"
        )

    temp_storage_prefix = (
        "실행 임시 파일을 준비하지 못했습니다. 권한, 용량, 경로를 확인하세요. 원인: "
    )
    if message.startswith(temp_storage_prefix):
        detail = message[len(temp_storage_prefix) :]
        return (
            "Could not prepare execution temporary files. "
            f"Check permissions, disk space, and paths. Cause: {detail}"
        )

    translated = _localize_runtime_fragments(message)
    if translated != message:
        return translated

    return None


def _localize_timeout_suffix(suffix: str) -> str:
    translated = suffix.replace("분", " min")
    translated = translated.replace("을 초과했습니다.", " was exceeded.")
    return translated


def _localize_runtime_fragments(message: str) -> str:
    replacements = (
        ("candidate 항목이 객체가 아닙니다", "candidate item is not an object"),
        ("prompt 항목이 객체가 아닙니다", "prompt item is not an object"),
        ("선택된 분석 후보 ", "selected analysis candidates "),
        ("건, 생성 프롬프트 ", ", generated prompts "),
        ("건입니다. 후보 작업 등록을 중단합니다.", ". Candidate job registration stopped."),
    )
    translated = message
    for source, target in replacements:
        translated = translated.replace(source, target)
    return translated


def localize_progress_line(line: str, language: str | None = None) -> str:
    """Translate a buffered progress log line when it is one of our known messages."""
    if normalize_ui_language(language) == SOURCE_RUNTIME_LANGUAGE:
        return _apply_progress_line_breaks(line)

    stripped = line.strip()
    exact = RUNTIME_EXACT_LINES.get(stripped)
    if exact is not None:
        _key, translated = exact
        return _apply_progress_line_breaks(translated)
    return _apply_progress_line_breaks(localize_runtime_message(line, language))


def _apply_progress_line_breaks(line: str) -> str:
    if "\\" not in line:
        return line

    parts: list[str] = []
    index = 0
    changed = False
    line_length = len(line)
    while index < line_length:
        if line[index] != "\\":
            parts.append(line[index])
            index += 1
            continue

        slash_start = index
        while index < line_length and line[index] == "\\":
            index += 1
        slash_count = index - slash_start
        if (
            index < line_length
            and slash_count % 2 == 1
            and line[index] in ("r", "n")
        ):
            parts.append("\\" * (slash_count // 2))
            escaped_char = line[index]
            index += 1
            parts.append("\n")
            changed = True
            if escaped_char == "r":
                index = _consume_following_newline_escape(line, index, parts)
            continue

        parts.append("\\" * slash_count)

    if not changed:
        return line
    return "".join(parts)


def _consume_following_newline_escape(
    line: str,
    index: int,
    parts: list[str],
) -> int:
    if index >= len(line) or line[index] != "\\":
        return index

    slash_start = index
    while index < len(line) and line[index] == "\\":
        index += 1

    slash_count = index - slash_start
    if index < len(line) and slash_count % 2 == 1 and line[index] == "n":
        parts.append("\\" * (slash_count // 2))
        return index + 1

    parts.append("\\" * slash_count)
    return index


class UiLocalizer:
    """Convenience wrapper that keeps one active language code."""

    def __init__(self, language: str | None) -> None:
        self.language = normalize_ui_language(language)

    def text(self, key: str, **values: object) -> str:
        """Return translated UI text for this localizer's language."""
        return text(key, self.language, **values)

    def runtime_message(self, message: str | None) -> str:
        """Translate a runtime message for this localizer's language."""
        return localize_runtime_message(message, self.language)

    def progress_line(self, line: str) -> str:
        """Translate a progress log line for this localizer's language."""
        return localize_progress_line(line, self.language)
