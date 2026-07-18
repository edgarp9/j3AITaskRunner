from __future__ import annotations

import unittest
from unittest.mock import patch

from ui.text_context_menu import (
    bind_editable_text_context_menu,
    bind_readonly_text_context_menu,
)


class EditableTextContextMenuTests(unittest.TestCase):
    def test_right_click_opens_localized_edit_menu(self) -> None:
        widget = _FakeTextWidget()
        event = _FakeMouseEvent(x=8, y=12, x_root=100, y_root=200)

        bind_editable_text_context_menu(widget, language="ko")
        with patch("ui.text_context_menu.tk.Menu", _FakeContextMenu):
            result = widget.bindings["<Button-3>"](event)

        self.assertEqual("break", result)
        self.assertEqual(["<Button-3>", "<Control-Button-1>"], widget.bound_sequences)
        self.assertEqual("2.4", widget.cursor_index)
        self.assertEqual([("sel", "1.0", "end")], widget.removed_tags)
        self.assertEqual(
            ["잘라내기", "복사", "붙여넣기", "모두 선택"],
            widget._editable_text_context_menu.command_labels,
        )
        self.assertEqual((100, 200), widget._editable_text_context_menu.popup_position)
        self.assertEqual(1, widget._editable_text_context_menu.separator_calls)
        self.assertEqual(1, widget._editable_text_context_menu.grab_release_calls)

        widget._editable_text_context_menu.commands[0]()
        widget._editable_text_context_menu.commands[1]()
        widget._editable_text_context_menu.commands[2]()
        widget._editable_text_context_menu.commands[3]()

        self.assertEqual(["<<Cut>>", "<<Copy>>", "<<Paste>>"], widget.generated_events)
        self.assertEqual([("sel", "1.0", "end")], widget.added_tags)
        self.assertEqual("1.0", widget.cursor_index)
        self.assertEqual(["insert"], widget.seen_indexes)

    def test_right_click_keeps_selection_when_pointer_is_inside_selection(self) -> None:
        widget = _FakeTextWidget(selection_ranges=("1.0", "3.0"))
        event = _FakeMouseEvent(x=8, y=12, x_root=100, y_root=200)

        bind_editable_text_context_menu(widget, language="en")
        with patch("ui.text_context_menu.tk.Menu", _FakeContextMenu):
            widget.bindings["<Button-3>"](event)

        self.assertIsNone(widget.cursor_index)
        self.assertEqual([], widget.removed_tags)
        self.assertEqual(
            ["Cut", "Copy", "Paste", "Select All"],
            widget._editable_text_context_menu.command_labels,
        )


class ReadonlyTextContextMenuTests(unittest.TestCase):
    def test_right_click_opens_copy_menu(self) -> None:
        widget = _FakeTextWidget()
        event = _FakeMouseEvent(x=8, y=12, x_root=100, y_root=200)

        bind_readonly_text_context_menu(widget, language="ko")
        with patch("ui.text_context_menu.tk.Menu", _FakeContextMenu):
            result = widget.bindings["<Button-3>"](event)

        self.assertEqual("break", result)
        self.assertEqual(["<Button-3>", "<Control-Button-1>"], widget.bound_sequences)
        self.assertEqual("2.4", widget.cursor_index)
        self.assertEqual([("sel", "1.0", "end")], widget.removed_tags)
        self.assertEqual(
            ["복사", "모두 선택"],
            widget._readonly_text_context_menu.command_labels,
        )
        self.assertEqual((100, 200), widget._readonly_text_context_menu.popup_position)
        self.assertEqual(1, widget._readonly_text_context_menu.separator_calls)
        self.assertEqual(1, widget._readonly_text_context_menu.grab_release_calls)

        widget._readonly_text_context_menu.commands[0]()
        widget._readonly_text_context_menu.commands[1]()

        self.assertEqual(["<<Copy>>"], widget.generated_events)
        self.assertEqual([("sel", "1.0", "end")], widget.added_tags)
        self.assertEqual("1.0", widget.cursor_index)
        self.assertEqual(["insert"], widget.seen_indexes)


class _FakeMouseEvent:
    def __init__(self, *, x: int, y: int, x_root: int, y_root: int) -> None:
        self.x = x
        self.y = y
        self.x_root = x_root
        self.y_root = y_root


class _FakeTextWidget:
    def __init__(self, *, selection_ranges: tuple[str, ...] = ()) -> None:
        self.bindings = {}
        self.bound_sequences: list[str] = []
        self.selection_ranges = selection_ranges
        self.cursor_index: str | None = None
        self.generated_events: list[str] = []
        self.added_tags: list[tuple[str, str, str]] = []
        self.removed_tags: list[tuple[str, str, str]] = []
        self.seen_indexes: list[str] = []

    def bind(self, sequence, func, add=None):  # noqa: ANN001
        self.bound_sequences.append(sequence)
        self.bindings[sequence] = func

    def focus_set(self) -> None:
        pass

    def index(self, index: str) -> str:
        self.requested_index = index
        return "2.4"

    def tag_ranges(self, tag: str) -> tuple[str, ...]:
        self.requested_tag = tag
        return self.selection_ranges

    def compare(self, left: str, operator: str, right: str) -> bool:
        order = {"1.0": 10, "2.4": 24, "3.0": 30}
        if operator == "<=":
            return order[left] <= order[right]
        if operator == "<":
            return order[left] < order[right]
        raise AssertionError(f"Unexpected comparison operator: {operator}")

    def tag_remove(self, tag: str, start: str, end: str) -> None:
        self.removed_tags.append((tag, start, end))

    def mark_set(self, mark: str, index: str) -> None:
        self.cursor_index = index

    def event_generate(self, sequence: str) -> None:
        self.generated_events.append(sequence)

    def tag_add(self, tag: str, start: str, end: str) -> None:
        self.added_tags.append((tag, start, end))

    def see(self, index: str) -> None:
        self.seen_indexes.append(index)


class _FakeContextMenu:
    def __init__(self, parent, *, tearoff: bool) -> None:  # noqa: ANN001
        self.parent = parent
        self.tearoff = tearoff
        self.command_labels: list[str] = []
        self.commands = []
        self.separator_calls = 0
        self.grab_release_calls = 0
        self.popup_position: tuple[int, int] | None = None

    def add_command(self, *, label: str, command) -> None:  # noqa: ANN001
        self.command_labels.append(label)
        self.commands.append(command)

    def add_separator(self) -> None:
        self.separator_calls += 1

    def tk_popup(self, x: int, y: int) -> None:
        self.popup_position = (x, y)

    def grab_release(self) -> None:
        self.grab_release_calls += 1


if __name__ == "__main__":
    unittest.main()
