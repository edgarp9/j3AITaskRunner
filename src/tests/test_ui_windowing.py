from __future__ import annotations

import unittest

from ui.windowing import center_toplevel


class ToplevelCenteringTests(unittest.TestCase):
    def test_center_toplevel_places_dialog_at_main_window_center(self) -> None:
        parent = _GeometryWidget(width=800, height=600, root_x=100, root_y=50)
        dialog = _GeometryWidget(width=1, height=1, req_width=300, req_height=200)

        center_toplevel(parent, dialog)  # type: ignore[arg-type]

        self.assertEqual(["300x200+350+250"], dialog.geometry_calls)

    def test_center_toplevel_preserves_negative_virtual_screen_coordinates(self) -> None:
        parent = _GeometryWidget(width=800, height=600, root_x=-1000, root_y=-700)
        dialog = _GeometryWidget(width=1, height=1, req_width=300, req_height=200)

        center_toplevel(parent, dialog)  # type: ignore[arg-type]

        self.assertEqual(["300x200+-750+-500"], dialog.geometry_calls)


class _GeometryWidget:
    def __init__(
        self,
        *,
        width: int,
        height: int,
        req_width: int | None = None,
        req_height: int | None = None,
        root_x: int = 0,
        root_y: int = 0,
    ) -> None:
        self._width = width
        self._height = height
        self._req_width = req_width if req_width is not None else width
        self._req_height = req_height if req_height is not None else height
        self._root_x = root_x
        self._root_y = root_y
        self.geometry_calls: list[str] = []

    def update_idletasks(self) -> None:
        pass

    def winfo_toplevel(self) -> "_GeometryWidget":
        return self

    def winfo_width(self) -> int:
        return self._width

    def winfo_height(self) -> int:
        return self._height

    def winfo_reqwidth(self) -> int:
        return self._req_width

    def winfo_reqheight(self) -> int:
        return self._req_height

    def winfo_rootx(self) -> int:
        return self._root_x

    def winfo_rooty(self) -> int:
        return self._root_y

    def geometry(self, value: str) -> None:
        self.geometry_calls.append(value)


if __name__ == "__main__":
    unittest.main()
