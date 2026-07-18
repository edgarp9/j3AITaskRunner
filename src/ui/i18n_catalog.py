"""Static UI translation catalog for Korean and English chrome."""

from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class LanguageOption:
    """Display label and stored code for one UI language option."""

    label: str
    value: str


from .i18n_runtime_catalog import (
    RUNTIME_EXACT_LINES,
    RUNTIME_MESSAGE_KEYS,
    RUNTIME_PREFIXES,
    RUNTIME_TRANSLATIONS,
)
from .i18n_ui_catalog import TRANSLATIONS
