"""Shared localization constants for user-facing language choices."""

from __future__ import annotations

DEFAULT_UI_LANGUAGE = "en"
SUPPORTED_UI_LANGUAGES = ("en", "ko")


def normalize_ui_language(value: str | None) -> str:
    """Return a supported UI language code, falling back to English."""
    normalized = (value or "").strip().lower()
    if normalized in SUPPORTED_UI_LANGUAGES:
        return normalized
    return DEFAULT_UI_LANGUAGE
