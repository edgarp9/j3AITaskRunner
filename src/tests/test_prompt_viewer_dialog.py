from __future__ import annotations

import unittest
from unittest.mock import patch

from ui.prompt_viewer_dialog import PromptViewerDialog


class PromptViewerDialogTests(unittest.TestCase):
    def test_prompt_editor_binds_readonly_context_menu(self) -> None:
        dialog = _PromptViewerDialogStub(language="ko")
        _FakeScrolledText.created = []

        with (
            patch("ui.prompt_viewer_dialog.ttk.Frame", _FakeFrame),
            patch("ui.prompt_viewer_dialog.ttk.Label", _FakeLabel),
            patch("ui.prompt_viewer_dialog.ttk.Button", _FakeButton),
            patch("ui.prompt_viewer_dialog.scrolledtext.ScrolledText", _FakeScrolledText),
            patch("ui.prompt_viewer_dialog.configure_text_widget") as configure_text,
            patch("ui.prompt_viewer_dialog.bind_readonly_text_context_menu") as bind_menu,
        ):
            PromptViewerDialog._build_widgets(
                dialog,
                job_id="job-1",
                prompt="full\nprompt",
            )

        prompt_text = _FakeScrolledText.created[0]
        configure_text.assert_called_once_with(prompt_text, scale=dialog._ui_scale)
        bind_menu.assert_called_once_with(
            prompt_text,
            menu_parent=dialog,
            language="ko",
        )
        self.assertEqual([("end", "full\nprompt")], prompt_text.inserted)
        self.assertEqual([{"state": "disabled"}], prompt_text.configured_options)


class _PromptViewerDialogStub:
    def __init__(self, *, language: str) -> None:
        self._language = language
        self._ui_scale = _IdentityUiScaleStub()
        self.column_weights: list[tuple[int, int]] = []
        self.row_weights: list[tuple[int, int]] = []

    def columnconfigure(self, column: int, *, weight: int) -> None:
        self.column_weights.append((column, weight))

    def rowconfigure(self, row: int, *, weight: int) -> None:
        self.row_weights.append((row, weight))

    def destroy(self) -> None:
        pass

    def _copy_prompt(self) -> None:
        pass


class _IdentityUiScaleStub:
    def padding(self, *values: int) -> int | tuple[int, ...]:
        if len(values) == 1:
            return values[0]
        return tuple(values)


class _FakeFrame:
    def __init__(self, parent, **options) -> None:  # noqa: ANN001
        self.parent = parent
        self.options = options
        self.grid_calls: list[dict[str, object]] = []
        self.column_weights: list[tuple[int, int]] = []
        self.row_weights: list[tuple[int, int]] = []

    def grid(self, **options: object) -> None:
        self.grid_calls.append(options)

    def columnconfigure(self, column: int, *, weight: int) -> None:
        self.column_weights.append((column, weight))

    def rowconfigure(self, row: int, *, weight: int) -> None:
        self.row_weights.append((row, weight))


class _FakeLabel:
    def __init__(self, parent, **options) -> None:  # noqa: ANN001
        self.parent = parent
        self.options = options
        self.grid_calls: list[dict[str, object]] = []

    def grid(self, **options: object) -> None:
        self.grid_calls.append(options)


class _FakeButton(_FakeLabel):
    pass


class _FakeScrolledText:
    created: list["_FakeScrolledText"] = []

    def __init__(self, parent, **options) -> None:  # noqa: ANN001
        self.parent = parent
        self.options = options
        self.inserted: list[tuple[str, str]] = []
        self.configured_options: list[dict[str, object]] = []
        self.grid_calls: list[dict[str, object]] = []
        self.created.append(self)

    def insert(self, index: str, text: str) -> None:
        self.inserted.append((index, text))

    def configure(self, **options: object) -> None:
        self.configured_options.append(options)

    def grid(self, **options: object) -> None:
        self.grid_calls.append(options)


if __name__ == "__main__":
    unittest.main()
