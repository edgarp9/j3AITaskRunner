from __future__ import annotations

import unittest

from app.agent_cli_options import (
    build_agent_provider_select_options,
    build_configured_agent_provider_select_options,
    build_model_select_options,
    build_reasoning_select_options,
)
from domain import AppSettings


class AgentCliOptionCatalogTests(unittest.TestCase):
    def test_provider_options_include_display_labels_and_stored_values(self) -> None:
        options = build_agent_provider_select_options("codex")

        self.assertEqual(
            [
                ("Codex CLI", "codex"),
                ("Claude Code", "claude_code"),
                ("Kilo Code", "kilo_code"),
                ("OpenCode", "opencode"),
                ("Pi Coding Agent", "pi"),
            ],
            [(option.label, option.value) for option in options],
        )

    def test_configured_provider_options_include_only_providers_with_executable(self) -> None:
        settings = AppSettings(
            agent_provider="opencode",
            executable_paths={
                "codex": r"C:\Tools\codex.exe",
                "opencode": r"C:\Tools\opencode.exe",
                "pi": "",
            },
        )

        options = build_configured_agent_provider_select_options("codex", settings)

        self.assertEqual(
            [("Codex CLI", "codex"), ("OpenCode", "opencode")],
            [(option.label, option.value) for option in options],
        )

    def test_codex_model_options_include_auto_and_known_models(self) -> None:
        options = build_model_select_options("")

        self.assertEqual(
            [
                "",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.5",
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-5.3-codex-spark",
            ],
            [option.value for option in options],
        )
        self.assertEqual("자동", options[0].label)

    def test_model_options_keep_legacy_value_without_duplication(self) -> None:
        options = build_model_select_options("legacy-model")

        legacy_options = [option for option in options if option.value == "legacy-model"]

        self.assertEqual(1, len(legacy_options))
        self.assertEqual("legacy-model (저장값)", legacy_options[0].label)

    def test_codex_model_options_do_not_keep_removed_saved_values(self) -> None:
        options = build_model_select_options("gpt-5")

        self.assertEqual(
            [
                "",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.5",
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-5.3-codex-spark",
            ],
            [option.value for option in options],
        )

    def test_non_default_provider_model_options_keep_only_auto_and_saved_value(self) -> None:
        options = build_model_select_options(
            "claude-opus-4.1",
            agent_provider="claude_code",
        )

        self.assertEqual(
            [("", "자동"), ("claude-opus-4.1", "claude-opus-4.1 (저장값)")],
            [(option.value, option.label) for option in options],
        )
        self.assertNotIn("gpt-5.5", [option.value for option in options])

    def test_codex_gpt5_reasoning_options_include_auto_and_supported_levels(self) -> None:
        options = build_reasoning_select_options("high", model="gpt-5.4")

        self.assertEqual("자동", options[0].label)
        self.assertEqual("", options[0].value)
        self.assertEqual(
            ["", "none", "minimal", "low", "medium", "high", "xhigh"],
            [option.value for option in options],
        )

    def test_codex_model_options_drop_invalid_generic_gpt56_saved_value(self) -> None:
        options = build_model_select_options("gpt-5.6")

        self.assertNotIn("gpt-5.6", [option.value for option in options])

    def test_codex_gpt56_sol_and_terra_reasoning_options_include_ultra(self) -> None:
        for model in ("gpt-5.6-sol", "gpt-5.6-terra"):
            with self.subTest(model=model):
                options = build_reasoning_select_options("ultra", model=model)

                self.assertEqual(
                    ["", "low", "medium", "high", "xhigh", "max", "ultra"],
                    [option.value for option in options],
                )

    def test_codex_gpt56_luna_reasoning_options_exclude_ultra(self) -> None:
        options = build_reasoning_select_options("max", model="gpt-5.6-luna")

        self.assertEqual(
            ["", "low", "medium", "high", "xhigh", "max"],
            [option.value for option in options],
        )

    def test_codex_reasoning_options_follow_model_family_matrix(self) -> None:
        supported_reasoning_models = (
            "",
            "gpt-5.5",
            "gpt-5.4-mini",
            "gpt-5.3-codex-spark",
        )

        for model in supported_reasoning_models:
            with self.subTest(model=model):
                options = build_reasoning_select_options("high", model=model)

                self.assertEqual(
                    ["", "none", "minimal", "low", "medium", "high", "xhigh"],
                    [option.value for option in options],
                )

    def test_codex_gpt41_reasoning_options_include_only_auto(self) -> None:
        options = build_reasoning_select_options("high", model="gpt-4.1")

        self.assertEqual([("", "자동")], [(option.value, option.label) for option in options])

    def test_codex_unknown_saved_model_uses_codex_reasoning_options(self) -> None:
        options = build_reasoning_select_options("high", model="saved-model")

        self.assertEqual(
            ["", "none", "minimal", "low", "medium", "high", "xhigh"],
            [option.value for option in options],
        )

    def test_reasoning_options_keep_legacy_value_without_duplication(self) -> None:
        options = build_reasoning_select_options("legacy-effort")

        legacy_options = [option for option in options if option.value == "legacy-effort"]

        self.assertEqual(1, len(legacy_options))
        self.assertEqual("legacy-effort (저장값)", legacy_options[0].label)

    def test_non_default_provider_reasoning_options_keep_only_auto_and_saved_value(self) -> None:
        options = build_reasoning_select_options(
            "sonnet",
            agent_provider="opencode",
        )

        self.assertEqual(
            [("", "자동"), ("sonnet", "sonnet (저장값)")],
            [(option.value, option.label) for option in options],
        )
        self.assertNotIn("high", [option.value for option in options])

    def test_pi_reasoning_options_include_documented_thinking_levels(self) -> None:
        options = build_reasoning_select_options(
            "high",
            agent_provider="pi",
            model="saved-model",
        )

        self.assertEqual(
            ["", "off", "minimal", "low", "medium", "high", "xhigh"],
            [option.value for option in options],
        )


if __name__ == "__main__":
    unittest.main()

