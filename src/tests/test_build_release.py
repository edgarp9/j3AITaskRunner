from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest
import zipfile

import build_release
from app.version import APP_NAME, APP_VERSION


class ReleasePackagingTests(unittest.TestCase):
    def test_pyinstaller_command_collects_prompt_markdown_under_prompt_tree(self) -> None:
        command = build_release.pyinstaller_command(Path("dist"), Path("build"))
        add_data_values = [
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--add-data"
        ]
        data_entries = [
            (Path(source), destination)
            for source, destination in (
                value.rsplit(build_release.os.pathsep, 1) for value in add_data_values
            )
        ]

        self.assertIn(
            (build_release.ASSETS_DIR / "app_icon.ico", "assets"),
            data_entries,
        )
        self.assertIn(
            (build_release.ASSETS_DIR / "app_icon.png", "assets"),
            data_entries,
        )
        self.assertIn(
            (build_release.ASSETS_DIR / "app_icon.svg", "assets"),
            data_entries,
        )
        self.assertIn(
            (build_release.PROJECT_LICENSE_FILE, "."),
            data_entries,
        )
        self.assertIn(
            (build_release.THIRD_PARTY_NOTICES_FILE, "."),
            data_entries,
        )
        self.assertIn(
            (build_release.ABOUT_FILE, "."),
            data_entries,
        )
        for static_license_file in build_release.STATIC_LICENSE_FILES:
            self.assertIn(
                (static_license_file, build_release.LICENSES_DESTINATION),
                data_entries,
            )

        prompt_files = set(build_release.prompt_markdown_files())
        prompt_entries = {
            source: destination
            for source, destination in data_entries
            if source in prompt_files
        }

        self.assertTrue(prompt_files)
        self.assertEqual(prompt_files, set(prompt_entries))
        for source, destination in prompt_entries.items():
            self.assertEqual(
                source.parent.relative_to(build_release.PROJECT_ROOT).as_posix(),
                destination,
            )
            self.assertNotEqual("assets", destination)
            self.assertTrue(destination.startswith("prompt/"))

    def test_release_notice_data_files_include_generated_python_license(self) -> None:
        license_file = Path("build") / "release-licenses" / "PYTHON-LICENSE.txt"

        self.assertEqual(
            (
                (build_release.PROJECT_LICENSE_FILE, "."),
                (build_release.THIRD_PARTY_NOTICES_FILE, "."),
                (build_release.ABOUT_FILE, "."),
                *(
                    (static_license_file, build_release.LICENSES_DESTINATION)
                    for static_license_file in build_release.STATIC_LICENSE_FILES
                ),
                (license_file, build_release.LICENSES_DESTINATION),
            ),
            build_release.release_notice_data_files((license_file,)),
        )

    def test_validate_binary_package_requires_bundled_notice_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            zip_path = Path(temp_dir) / "binary.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr(
                    f"{APP_NAME}/{build_release.LIB_DIR_NAME}/LICENSE",
                    "license",
                )
                archive.writestr(
                    f"{APP_NAME}/{build_release.LIB_DIR_NAME}/THIRD_PARTY_NOTICES.txt",
                    "notices",
                )
                archive.writestr(
                    f"{APP_NAME}/{build_release.LIB_DIR_NAME}/about.txt",
                    "about",
                )

            build_release.validate_binary_package(zip_path)

    def test_validate_binary_package_reports_missing_notice_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            zip_path = Path(temp_dir) / "binary.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr(
                    f"{APP_NAME}/{build_release.LIB_DIR_NAME}/LICENSE",
                    "license",
                )

            with self.assertRaises(build_release.BuildError) as context:
                build_release.validate_binary_package(zip_path)

        self.assertIn("THIRD_PARTY_NOTICES.txt", str(context.exception))
        self.assertIn("about.txt", str(context.exception))

    def test_prepare_release_license_files_copies_required_licenses(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_license = temp_path / "LICENSE.txt"
            source_license.write_text("Python license sample", encoding="utf-8")
            package_licenses = {
                ("pyinstaller", "COPYING.txt"): temp_path / "PYINSTALLER-COPYING.txt",
                ("tkinterdnd2", "LICENSE"): temp_path / "TKINTERDND2-LICENSE.txt",
            }
            for path in package_licenses.values():
                path.write_text(f"{path.name} sample", encoding="utf-8")

            with mock.patch.object(
                build_release,
                "python_license_source_path",
                return_value=source_license,
            ), mock.patch.object(
                build_release,
                "package_license_source_path",
                side_effect=lambda package_name, license_name: package_licenses[
                    (package_name, license_name)
                ],
            ):
                license_files = build_release.prepare_release_license_files(
                    temp_path / "build"
                )

            copied_by_name = {path.name: path for path in license_files}
            self.assertEqual(
                {
                    build_release.PYTHON_LICENSE_COPY_NAME,
                    *build_release.PACKAGE_LICENSE_COPY_NAMES,
                },
                set(copied_by_name),
            )
            self.assertEqual(
                "Python license sample",
                copied_by_name[build_release.PYTHON_LICENSE_COPY_NAME].read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                "PYINSTALLER-COPYING.txt sample",
                copied_by_name["PYINSTALLER-COPYING.txt"].read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "TKINTERDND2-LICENSE.txt sample",
                copied_by_name["TKINTERDND2-LICENSE.txt"].read_text(encoding="utf-8"),
            )

    def test_version_info_file_uses_app_version_constants(self) -> None:
        with TemporaryDirectory() as temp_dir:
            version_info_file = build_release.write_version_info_file(Path(temp_dir))

            version_info_text = version_info_file.read_text(encoding="utf-8")

        self.assertIn(
            f"StringStruct('FileDescription', '{APP_NAME}')",
            version_info_text,
        )
        self.assertIn(
            f"StringStruct('FileVersion', '{APP_VERSION}')",
            version_info_text,
        )
        self.assertIn(
            f"StringStruct('ProductVersion', '{APP_VERSION}')",
            version_info_text,
        )

    def test_build_venv_dir_is_platform_specific(self) -> None:
        self.assertEqual(
            build_release.BUILD_VENV_ROOT / "linux",
            build_release.build_venv_dir("linux"),
        )
        self.assertEqual(
            build_release.BUILD_VENV_ROOT / "windows",
            build_release.build_venv_dir("windows"),
        )

    def test_remove_existing_source_packages_deletes_platform_source_zips_only(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            platform_dist_dir = Path(temp_dir)
            current_source_zip = platform_dist_dir / f"{APP_NAME}-{APP_VERSION}-source.zip"
            old_source_zip = platform_dist_dir / f"{APP_NAME}-0.2.0-source.zip"
            binary_zip = platform_dist_dir / build_release.binary_package_name("windows")
            nested_dir = platform_dist_dir / "nested"
            nested_source_zip = nested_dir / f"{APP_NAME}-0.1.0-source.zip"
            nested_dir.mkdir()
            for path in (
                current_source_zip,
                old_source_zip,
                binary_zip,
                nested_source_zip,
            ):
                path.write_text(path.name, encoding="utf-8")

            removed_paths = build_release.remove_existing_source_packages(
                platform_dist_dir
            )

            self.assertEqual(
                {current_source_zip, old_source_zip},
                set(removed_paths),
            )
            self.assertFalse(current_source_zip.exists())
            self.assertFalse(old_source_zip.exists())
            self.assertTrue(binary_zip.exists())
            self.assertTrue(nested_source_zip.exists())

    def test_venv_python_path_uses_platform_specific_layout(self) -> None:
        venv_dir = Path(".build-venv") / "example"

        self.assertEqual(
            venv_dir / "bin" / "python",
            build_release.venv_python_path(venv_dir, "Linux"),
        )
        self.assertEqual(
            venv_dir / "Scripts" / "python.exe",
            build_release.venv_python_path(venv_dir, "Windows"),
        )

    def test_bootstrap_build_environment_reexecs_managed_venv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            venv_dir = Path(temp_dir) / ".build-venv" / "linux"
            venv_python = venv_dir / "bin" / "python"
            run_result = mock.Mock(returncode=7)

            with (
                mock.patch.dict(build_release.os.environ, {}, clear=True),
                mock.patch.object(build_release, "build_venv_dir", return_value=venv_dir),
                mock.patch.object(
                    build_release,
                    "venv_python_path",
                    return_value=venv_python,
                ),
                mock.patch.object(build_release, "ensure_build_venv") as ensure_venv,
                mock.patch.object(
                    build_release,
                    "ensure_build_requirements",
                ) as ensure_requirements,
                mock.patch.object(
                    build_release.subprocess,
                    "run",
                    return_value=run_result,
                ) as run,
                mock.patch("builtins.print"),
            ):
                exit_code = build_release.bootstrap_build_environment(["--sample"])

            self.assertEqual(7, exit_code)
            ensure_venv.assert_called_once_with(venv_dir, venv_python)
            ensure_requirements.assert_called_once_with(venv_python)
            run.assert_called_once()
            self.assertEqual(
                [
                    str(venv_python),
                    str(Path(build_release.__file__).resolve()),
                    "--sample",
                ],
                run.call_args.args[0],
            )
            self.assertEqual(build_release.PROJECT_ROOT, run.call_args.kwargs["cwd"])
            self.assertEqual(
                "1",
                run.call_args.kwargs["env"][build_release.BUILD_BOOTSTRAP_ENV_VAR],
            )

    def test_bootstrap_reexecs_when_venv_python_symlinks_to_system_python(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir)
            base_python = root_dir / "usr" / "bin" / "python3"
            venv_dir = root_dir / ".build-venv" / "linux"
            venv_python = venv_dir / "bin" / "python"
            base_python.parent.mkdir(parents=True)
            venv_python.parent.mkdir(parents=True)
            base_python.touch()
            try:
                os.symlink(base_python, venv_python)
            except OSError as exc:
                self.skipTest(f"symlink creation is not available: {exc}")

            run_result = mock.Mock(returncode=0)

            with (
                mock.patch.dict(build_release.os.environ, {}, clear=True),
                mock.patch.object(build_release.sys, "executable", str(base_python)),
                mock.patch.object(build_release.sys, "prefix", str(root_dir / "usr")),
                mock.patch.object(build_release, "build_venv_dir", return_value=venv_dir),
                mock.patch.object(
                    build_release,
                    "venv_python_path",
                    return_value=venv_python,
                ),
                mock.patch.object(build_release, "ensure_build_venv") as ensure_venv,
                mock.patch.object(
                    build_release,
                    "ensure_build_requirements",
                ) as ensure_requirements,
                mock.patch.object(
                    build_release.subprocess,
                    "run",
                    return_value=run_result,
                ) as run,
                mock.patch("builtins.print"),
            ):
                exit_code = build_release.bootstrap_build_environment(["--sample"])

            self.assertEqual(0, exit_code)
            ensure_venv.assert_called_once_with(venv_dir, venv_python)
            ensure_requirements.assert_called_once_with(venv_python)
            self.assertEqual(str(venv_python), run.call_args.args[0][0])

    def test_bootstrap_build_environment_skips_when_child_marker_is_set(self) -> None:
        with (
            mock.patch.dict(
                build_release.os.environ,
                {build_release.BUILD_BOOTSTRAP_ENV_VAR: "1"},
                clear=True,
            ),
            mock.patch.object(build_release, "ensure_build_venv") as ensure_venv,
            mock.patch.object(
                build_release,
                "ensure_build_requirements",
            ) as ensure_requirements,
            mock.patch.object(build_release.subprocess, "run") as run,
        ):
            exit_code = build_release.bootstrap_build_environment()

        self.assertIsNone(exit_code)
        ensure_venv.assert_not_called()
        ensure_requirements.assert_not_called()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
