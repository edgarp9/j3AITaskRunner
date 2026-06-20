"""Provider-aware option catalogs for session and preset agent CLI runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from domain import (
    DEFAULT_AGENT_PROVIDER,
    SUPPORTED_AGENT_PROVIDERS,
    AppSettings,
    normalize_agent_provider,
)


@dataclass(frozen=True, slots=True)
class SelectOption:
    """Display label and persisted value for one option choice."""

    label: str
    value: str


SUPPORTED_CODEX_MODEL_IDS: Final[tuple[str, ...]] = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5-codex",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
    "gpt-5-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "o4-mini",
)

SUPPORTED_REASONING_EFFORTS: Final[tuple[str, ...]] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)
SUPPORTED_PI_THINKING_LEVELS: Final[tuple[str, ...]] = (
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)

AUTO_MODEL_LABEL: Final[str] = "자동"
AUTO_REASONING_LABEL: Final[str] = "자동"
AGENT_PROVIDER_LABELS: Final[dict[str, str]] = {
    "codex": "Codex CLI",
    "claude_code": "Claude Code",
    "kilo_code": "Kilo Code",
    "opencode": "OpenCode",
    "pi": "Pi Coding Agent",
}

_PROVIDER_MODEL_IDS: Final[dict[str, tuple[str, ...]]] = {
    DEFAULT_AGENT_PROVIDER: SUPPORTED_CODEX_MODEL_IDS,
    "claude_code": (),
    "kilo_code": (),
    "opencode": (),
    "pi": (),
}

_PROVIDER_REASONING_OPTIONS: Final[dict[str, tuple[str, ...]]] = {
    DEFAULT_AGENT_PROVIDER: SUPPORTED_REASONING_EFFORTS,
    "claude_code": (),
    "kilo_code": (),
    "opencode": (),
    "pi": SUPPORTED_PI_THINKING_LEVELS,
}


def build_agent_provider_select_options(
    current_value: str | None,
) -> tuple[SelectOption, ...]:
    """Return provider choices for agent CLI option controls."""
    normalized_current_value = normalize_agent_provider(current_value)
    options = [
        SelectOption(
            label=AGENT_PROVIDER_LABELS.get(provider_id, provider_id),
            value=provider_id,
        )
        for provider_id in SUPPORTED_AGENT_PROVIDERS
    ]
    if normalized_current_value not in {option.value for option in options}:
        options.append(
            SelectOption(
                label=AGENT_PROVIDER_LABELS.get(
                    normalized_current_value,
                    normalized_current_value,
                ),
                value=normalized_current_value,
            )
        )
    return tuple(options)


def build_configured_agent_provider_select_options(
    current_value: str | None,
    settings: AppSettings,
) -> tuple[SelectOption, ...]:
    """Return provider choices that have an executable configured."""
    normalized_current_value = normalize_agent_provider(current_value)
    configured_providers = {
        normalize_agent_provider(provider_id)
        for provider_id, executable_path in settings.executable_paths.items()
        if isinstance(executable_path, str) and executable_path.strip()
    }
    if settings.executable_path:
        configured_providers.add(normalize_agent_provider(settings.agent_provider))

    ordered_providers = [
        provider_id
        for provider_id in SUPPORTED_AGENT_PROVIDERS
        if provider_id in configured_providers
    ]
    if (
        normalized_current_value in configured_providers
        and normalized_current_value not in ordered_providers
    ):
        ordered_providers.append(normalized_current_value)

    return tuple(
        SelectOption(
            label=AGENT_PROVIDER_LABELS.get(provider_id, provider_id),
            value=provider_id,
        )
        for provider_id in ordered_providers
    )


def build_model_select_options(
    current_value: str | None,
    *,
    agent_provider: str | None = DEFAULT_AGENT_PROVIDER,
    auto_label: str = AUTO_MODEL_LABEL,
    saved_value_suffix: str = "저장값",
) -> tuple[SelectOption, ...]:
    """Return model choices for agent CLI option controls."""
    normalized_provider = normalize_agent_provider(agent_provider)
    return _build_select_options(
        auto_label=auto_label,
        saved_value_suffix=saved_value_suffix,
        supported_values=_PROVIDER_MODEL_IDS.get(normalized_provider, ()),
        current_value=current_value,
    )


def build_reasoning_select_options(
    current_value: str | None,
    *,
    agent_provider: str | None = DEFAULT_AGENT_PROVIDER,
    model: str | None = None,
    auto_label: str = AUTO_REASONING_LABEL,
    saved_value_suffix: str = "저장값",
) -> tuple[SelectOption, ...]:
    """Return reasoning effort choices for agent CLI option controls."""
    normalized_provider = normalize_agent_provider(agent_provider)
    if normalized_provider == DEFAULT_AGENT_PROVIDER:
        supported_values = _codex_reasoning_efforts_for_model(model)
        if not supported_values:
            return (SelectOption(label=auto_label, value=""),)
        return _build_select_options(
            auto_label=auto_label,
            saved_value_suffix=saved_value_suffix,
            supported_values=supported_values,
            current_value=current_value,
        )

    return _build_select_options(
        auto_label=auto_label,
        saved_value_suffix=saved_value_suffix,
        supported_values=_PROVIDER_REASONING_OPTIONS.get(normalized_provider, ()),
        current_value=current_value,
    )


def find_option_label(options: tuple[SelectOption, ...], current_value: str | None) -> str:
    """Resolve the display label for a persisted value."""
    normalized_value = (current_value or "").strip()
    for option in options:
        if option.value == normalized_value:
            return option.label
    return normalized_value


def _codex_reasoning_efforts_for_model(model: str | None) -> tuple[str, ...]:
    normalized_model = (model or "").strip().lower()
    if normalized_model.startswith("gpt-4.1"):
        return ()
    return SUPPORTED_REASONING_EFFORTS


def _build_select_options(
    *,
    auto_label: str,
    saved_value_suffix: str,
    supported_values: tuple[str, ...],
    current_value: str | None,
) -> tuple[SelectOption, ...]:
    normalized_current_value = (current_value or "").strip()
    options: list[SelectOption] = [SelectOption(label=auto_label, value="")]
    seen_values = {""}

    for supported_value in supported_values:
        normalized_supported_value = supported_value.strip()
        if not normalized_supported_value or normalized_supported_value in seen_values:
            continue
        options.append(
            SelectOption(
                label=normalized_supported_value,
                value=normalized_supported_value,
            )
        )
        seen_values.add(normalized_supported_value)

    if normalized_current_value and normalized_current_value not in seen_values:
        options.append(
            SelectOption(
                label=f"{normalized_current_value} ({saved_value_suffix})",
                value=normalized_current_value,
            )
        )

    return tuple(options)
