from __future__ import annotations

import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import main
from app.version import APP_NAME, APP_VERSION


class StorageRootResolutionTests(unittest.TestCase):
    def test_resolve_default_storage_root_uses_script_directory(self) -> None:
        with patch.object(main, "__file__", r"C:\Apps\j3AITaskRunner\main.py"):
            with patch.object(main.sys, "frozen", False, create=True):
                resolved = main.resolve_default_storage_root()

        self.assertEqual(Path(r"C:\Apps\j3AITaskRunner"), resolved)

    def test_resolve_default_storage_root_uses_executable_directory_when_frozen(self) -> None:
        with patch.object(main.sys, "frozen", True, create=True):
            with patch.object(main.sys, "executable", r"C:\Dist\j3AITaskRunner.exe"):
                resolved = main.resolve_default_storage_root()

        self.assertEqual(Path(r"C:\Dist"), resolved)


class MainWindowBuildTests(unittest.TestCase):
    def test_build_main_window_configures_dpi_before_creating_window(self) -> None:
        calls: list[str] = []

        with (
            patch("main.configure_windows_dpi_awareness", side_effect=lambda: calls.append("dpi")),
            patch("main.build_runtime", side_effect=lambda *, storage_root=None: calls.append("runtime") or object()),
            patch("main.MainWindow", side_effect=lambda runtime: calls.append("window") or runtime),
        ):
            window = main.build_main_window(storage_root=Path(r"C:\Apps"))

        self.assertIsNotNone(window)
        self.assertEqual(["dpi", "runtime", "window"], calls)

    def test_build_main_window_can_use_separate_app_base_dir(self) -> None:
        with (
            patch("main.configure_windows_dpi_awareness"),
            patch("main.build_runtime") as build_runtime,
            patch("main.MainWindow", side_effect=lambda runtime: runtime),
        ):
            main.build_main_window(
                storage_root=Path(r"C:\Data"),
                app_base_dir=Path(r"C:\Apps"),
            )

        build_runtime.assert_called_once_with(
            storage_root=Path(r"C:\Data"),
            app_base_dir=Path(r"C:\Apps"),
        )

    def test_build_runtime_places_watch_dir_under_app_base_dir(self) -> None:
        with (
            patch("main.LocalJsonRepository"),
            patch("main.PromptStore"),
            patch("main.ProviderAgentCliProcessRunner"),
            patch("main.SystemSleepPreventer"),
            patch("main.AppController"),
            patch("main.AppRuntime") as runtime_cls,
        ):
            runtime = main.build_runtime(
                storage_root=Path(r"C:\Data"),
                app_base_dir=Path(r"C:\Apps"),
            )

        self.assertIs(runtime_cls.return_value, runtime)
        self.assertEqual(
            Path(r"C:\Apps") / "watch",
            runtime_cls.call_args.kwargs["file_drop_dir"],
        )


class CommandLineTests(unittest.TestCase):
    def test_main_version_option_exits_without_building_window(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with patch("main.build_main_window") as build_main_window:
                with self.assertRaises(SystemExit) as captured:
                    main.main(["--version"])

        self.assertEqual(0, captured.exception.code)
        self.assertEqual(f"{APP_NAME} {APP_VERSION}\n", stdout.getvalue())
        build_main_window.assert_not_called()

    def test_main_help_option_exits_without_building_window(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with patch("main.build_main_window") as build_main_window:
                with self.assertRaises(SystemExit) as captured:
                    main.main(["--help"])

        self.assertEqual(0, captured.exception.code)
        self.assertIn(APP_NAME, stdout.getvalue())
        build_main_window.assert_not_called()

    def test_main_launches_window_without_cli_options(self) -> None:
        with (
            patch("main.configure_logging"),
            patch("main.build_main_window") as build_main_window,
        ):
            window = build_main_window.return_value
            exit_code = main.main([])

        self.assertEqual(0, exit_code)
        build_main_window.assert_called_once_with()
        window.open_startup_workspaces.assert_not_called()
        window.run.assert_called_once_with()

    def test_main_uses_data_dir_for_storage_without_moving_app_assets(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage_root = Path(storage_dir).resolve()
            with (
                patch("main.configure_logging"),
                patch("main.resolve_default_storage_root", return_value=Path(r"C:\Apps")),
                patch("main.build_main_window") as build_main_window,
            ):
                window = build_main_window.return_value
                exit_code = main.main(["--data-dir", storage_dir])

        self.assertEqual(0, exit_code)
        build_main_window.assert_called_once_with(
            storage_root=storage_root,
            app_base_dir=Path(r"C:\Apps"),
        )
        window.open_startup_workspaces.assert_not_called()
        window.run.assert_called_once_with()

    def test_main_opens_workspace_paths_from_cli_after_building_window(self) -> None:
        with TemporaryDirectory() as first_workspace:
            with TemporaryDirectory() as second_workspace:
                expected_paths = (
                    str(Path(first_workspace).resolve()),
                    str(Path(second_workspace).resolve()),
                )
                with (
                    patch("main.configure_logging"),
                    patch("main.build_main_window") as build_main_window,
                ):
                    window = build_main_window.return_value
                    exit_code = main.main([first_workspace, second_workspace])

        self.assertEqual(0, exit_code)
        build_main_window.assert_called_once_with()
        window.open_startup_workspaces.assert_called_once_with(expected_paths)
        window.run.assert_called_once_with()

    def test_resolve_startup_workspace_paths_resolves_relative_paths_from_cwd(
        self,
    ) -> None:
        with TemporaryDirectory() as working_dir:
            workspace_dir = Path(working_dir) / "relative-workspace"
            workspace_dir.mkdir()
            resolved = main.resolve_startup_workspace_paths(
                ("relative-workspace",),
                base_dir=Path(working_dir),
            )

            self.assertEqual((str(workspace_dir.resolve()),), resolved)

    def test_resolve_data_dir_path_resolves_relative_path_from_cwd(self) -> None:
        with TemporaryDirectory() as working_dir:
            resolved = main.resolve_data_dir_path(
                "profile",
                base_dir=Path(working_dir),
            )

            self.assertEqual((Path(working_dir) / "profile").resolve(), resolved)

    def test_parse_args_accepts_workspace_paths(self) -> None:
        args = main.parse_args(["alpha", "beta"])

        self.assertEqual(["alpha", "beta"], args.workspace_paths)

    def test_parse_help_exits_without_building_window(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with patch("main.build_main_window") as build_main_window:
                with self.assertRaises(SystemExit) as captured:
                    main.parse_args(["--help"])

        self.assertEqual(0, captured.exception.code)
        build_main_window.assert_not_called()


if __name__ == "__main__":
    unittest.main()
