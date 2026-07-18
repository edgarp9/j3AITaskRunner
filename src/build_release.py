"""Build a PyInstaller onedir release for j3AITaskRunner."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import zipfile

from app.version import APP_NAME, APP_VERSION

from tools.release_version_info import (
    _format_version_info_file,
    _windows_version_tuple,
    version_info_file_path,
    write_version_info_file,
)

LIB_DIR_NAME = "lib"

PROJECT_ROOT = Path(__file__).resolve().parent
ENTRYPOINT = PROJECT_ROOT / "main.py"
ASSETS_DIR = PROJECT_ROOT / "assets"
PROMPT_DIR = PROJECT_ROOT / "prompt"
THIRD_PARTY_NOTICES_FILE = PROJECT_ROOT / "THIRD_PARTY_NOTICES.txt"
PROJECT_LICENSE_FILE = PROJECT_ROOT / "LICENSE"
ABOUT_FILE = PROJECT_ROOT / "about.txt"
LICENSES_DIR = PROJECT_ROOT / "LICENSES"
ICON_FILE = ASSETS_DIR / "app_icon.ico"
BUNDLED_ASSET_FILES = (
    ASSETS_DIR / "app_icon.ico",
    ASSETS_DIR / "app_icon.png",
    ASSETS_DIR / "app_icon.svg",
)
LICENSES_DESTINATION = "licenses"
STATIC_LICENSE_FILES = (LICENSES_DIR / "APACHE-2.0.txt",)
PYTHON_LICENSE_COPY_NAME = "PYTHON-LICENSE.txt"
PYTHON_LICENSE_CANDIDATE_NAMES = ("LICENSE.txt", "LICENSE")
PACKAGE_LICENSE_COPY_TARGETS = (
    ("pyinstaller", "COPYING.txt", "PYINSTALLER-COPYING.txt"),
    ("tkinterdnd2", "LICENSE", "TKINTERDND2-LICENSE.txt"),
)
PACKAGE_LICENSE_COPY_NAMES = tuple(
    copy_name for _package_name, _license_name, copy_name in PACKAGE_LICENSE_COPY_TARGETS
)
OPTIONAL_COLLECT_ALL_MODULES = ("tkinterdnd2",)
SOURCE_PACKAGE_GLOB = f"{APP_NAME}-*-source.zip"

DIST_ROOT = PROJECT_ROOT / "dist"
BUILD_ROOT = PROJECT_ROOT / "build"
VERSION_INFO_FILE_NAME = "version_info.txt"
BUILD_VENV_ROOT = PROJECT_ROOT / ".build-venv"
BUILD_BOOTSTRAP_ENV_VAR = "J3AITASKRUNNER_BUILD_VENV_READY"
BUILD_REQUIREMENTS = ("pyinstaller>=6", "tkinterdnd2")
BUILD_REQUIREMENTS_CHECK_CODE = """
import importlib.metadata
import importlib.util

try:
    pyinstaller_version = importlib.metadata.version("pyinstaller")
except importlib.metadata.PackageNotFoundError:
    raise SystemExit(1)

major_text = pyinstaller_version.split(".", 1)[0]
if not major_text.isdigit() or int(major_text) < 6:
    raise SystemExit(1)

if importlib.util.find_spec("tkinterdnd2") is None:
    raise SystemExit(1)
"""


class BuildError(RuntimeError):
    """Raised when the release build cannot be started or validated."""


def platform_name() -> str:
    """Return the release folder name for the current OS."""
    system_name = platform.system().lower()
    if system_name == "windows":
        return "windows"
    if system_name == "linux":
        return "linux"
    return re.sub(r"[^a-z0-9_.-]+", "-", system_name).strip("-") or "unknown"


def executable_name() -> str:
    """Return the app executable filename for the current OS."""
    if platform.system().lower() == "windows":
        return f"{APP_NAME}.exe"
    return APP_NAME


def build_venv_dir(current_platform: str | None = None) -> Path:
    """Return the managed build virtual environment directory."""
    return BUILD_VENV_ROOT / (current_platform or platform_name())


def venv_python_path(venv_dir: Path, system_name: str | None = None) -> Path:
    """Return the Python executable path inside a virtual environment."""
    resolved_system = (system_name or platform.system()).lower()
    if resolved_system == "windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def same_path(left: Path, right: Path) -> bool:
    """Return whether two paths have the same normalized absolute spelling."""
    left_text = os.path.normcase(os.path.abspath(os.fspath(left)))
    right_text = os.path.normcase(os.path.abspath(os.fspath(right)))
    return left_text == right_text


def current_python_uses_venv(venv_dir: Path) -> bool:
    """Return whether the current interpreter is running from the build venv."""
    return same_path(Path(sys.prefix), venv_dir)


def run_bootstrap_command(command: list[str], *, description: str) -> None:
    """Run a setup command required before invoking PyInstaller."""
    print(description)
    print(f"Command: {command_for_display(command)}")
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        raise BuildError(f"{description} failed with exit code {result.returncode}.")


def command_succeeds(command: list[str]) -> bool:
    """Return whether a setup probe command exits successfully."""
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def ensure_build_venv(venv_dir: Path, venv_python: Path) -> None:
    """Create the managed build virtual environment when needed."""
    if venv_python.exists():
        return

    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    run_bootstrap_command(
        [sys.executable, "-m", "venv", str(venv_dir)],
        description=f"Creating build virtual environment: {venv_dir}",
    )
    if not venv_python.exists():
        raise BuildError(f"Virtual environment Python was not created: {venv_python}")


def ensure_pip(venv_python: Path) -> None:
    """Ensure pip is available inside the managed build virtual environment."""
    pip_command = [str(venv_python), "-m", "pip", "--version"]
    if command_succeeds(pip_command):
        return

    run_bootstrap_command(
        [str(venv_python), "-m", "ensurepip", "--upgrade"],
        description=f"Installing pip in build virtual environment: {venv_python}",
    )
    if not command_succeeds(pip_command):
        raise BuildError(f"pip is not available in build virtual environment: {venv_python}")


def build_requirements_installed(python_executable: Path) -> bool:
    """Return whether required build packages are ready in one interpreter."""
    return command_succeeds(
        [str(python_executable), "-c", BUILD_REQUIREMENTS_CHECK_CODE]
    )


def ensure_build_requirements(python_executable: Path) -> None:
    """Install or update build packages in the managed build environment."""
    if build_requirements_installed(python_executable):
        return

    ensure_pip(python_executable)
    run_bootstrap_command(
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--upgrade",
            *BUILD_REQUIREMENTS,
        ],
        description=(
            "Installing build dependencies: "
            + ", ".join(BUILD_REQUIREMENTS)
        ),
    )
    if not build_requirements_installed(python_executable):
        raise BuildError(
            "Build dependencies are still unavailable after installation."
        )


def build_reexec_command(venv_python: Path, argv: list[str] | None = None) -> list[str]:
    """Return the command that reruns this script inside the build venv."""
    return [str(venv_python), str(Path(__file__).resolve()), *(argv or sys.argv[1:])]


def bootstrap_build_environment(argv: list[str] | None = None) -> int | None:
    """Prepare the managed build venv and rerun this script in it.

    Return a process exit code when a child process handled the build, or None
    when the current process should continue with the release build.
    """
    if os.environ.get(BUILD_BOOTSTRAP_ENV_VAR) == "1":
        return None

    venv_dir = build_venv_dir()
    venv_python = venv_python_path(venv_dir)
    if current_python_uses_venv(venv_dir):
        ensure_build_requirements(Path(sys.executable))
        return None

    ensure_build_venv(venv_dir, venv_python)
    ensure_build_requirements(venv_python)

    command = build_reexec_command(venv_python, argv)
    child_env = os.environ.copy()
    child_env[BUILD_BOOTSTRAP_ENV_VAR] = "1"
    print(f"Build virtual environment: {venv_dir}")
    print(f"Re-running release build: {command_for_display(command)}")
    result = subprocess.run(command, cwd=PROJECT_ROOT, env=child_env, check=False)
    return result.returncode


def pyinstaller_version() -> str:
    """Return the installed PyInstaller version or raise a build error."""
    try:
        version_text = importlib.metadata.version("pyinstaller")
    except importlib.metadata.PackageNotFoundError as exc:
        raise BuildError(
            "PyInstaller is not installed. Install it with: "
            f"{sys.executable} -m pip install pyinstaller"
        ) from exc

    match = re.match(r"(\d+)", version_text)
    if not match:
        raise BuildError(f"Cannot parse PyInstaller version: {version_text}")
    if int(match.group(1)) < 6:
        raise BuildError(
            "PyInstaller 6 or newer is required because this build uses "
            "--contents-directory lib."
        )
    return version_text


def ensure_required_files() -> None:
    """Validate that the files required for the release bundle exist."""
    required_paths = (
        ENTRYPOINT,
        *BUNDLED_ASSET_FILES,
        PROMPT_DIR,
        PROJECT_LICENSE_FILE,
        THIRD_PARTY_NOTICES_FILE,
        ABOUT_FILE,
        *STATIC_LICENSE_FILES,
    )
    missing_paths = [path for path in required_paths if not path.exists()]
    if missing_paths:
        formatted = "\n".join(f"  - {path}" for path in missing_paths)
        raise BuildError(f"Required path(s) are missing:\n{formatted}")
    if not prompt_markdown_files():
        prompt_pattern = PROMPT_DIR / "**" / "*.md"
        raise BuildError(f"Required prompt markdown files are missing: {prompt_pattern}")


def ensure_project_path(path: Path) -> Path:
    """Resolve a path and ensure it is inside the project root."""
    resolved_project_root = PROJECT_ROOT.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_project_root)
    except ValueError as exc:
        raise BuildError(f"Refusing to operate outside project root: {resolved_path}") from exc
    return resolved_path


def remove_existing_path(path: Path) -> None:
    """Remove an old build path after checking that it is project-local."""
    resolved_path = ensure_project_path(path)
    if not resolved_path.exists():
        return
    if resolved_path.is_dir():
        shutil.rmtree(resolved_path)
        return
    resolved_path.unlink()


def add_data_argument(source: Path, destination: str) -> str:
    """Return a PyInstaller --add-data argument for the current platform."""
    return f"{source}{os.pathsep}{destination}"


def prompt_markdown_files() -> tuple[Path, ...]:
    """Return prompt markdown assets that must be bundled with the app."""
    if not PROMPT_DIR.is_dir():
        return ()
    return tuple(sorted(path for path in PROMPT_DIR.rglob("*.md") if path.is_file()))


def prompt_data_destination(prompt_file: Path) -> str:
    """Return the PyInstaller destination directory for one prompt asset."""
    relative_parent = prompt_file.parent.relative_to(PROJECT_ROOT)
    return relative_parent.as_posix()


def python_license_source_path() -> Path | None:
    """Return the current interpreter license file when it is discoverable."""
    candidate_roots = [
        Path(sys.base_prefix),
        Path(sys.prefix),
        Path(sysconfig.get_path("stdlib")),
    ]
    for root in candidate_roots:
        for file_name in PYTHON_LICENSE_CANDIDATE_NAMES:
            candidate = root / file_name
            if candidate.is_file():
                return candidate
    return None


def package_license_source_path(package_name: str, license_file_name: str) -> Path:
    """Return a package license file from installed distribution metadata."""
    try:
        distribution = importlib.metadata.distribution(package_name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise BuildError(
            f"Required package for license collection is not installed: {package_name}"
        ) from exc

    for distribution_file in distribution.files or ():
        normalized_file = str(distribution_file).replace("\\", "/")
        if Path(normalized_file).name != license_file_name:
            continue
        candidate = Path(distribution.locate_file(distribution_file))
        if candidate.is_file():
            return candidate

    raise BuildError(
        f"License file {license_file_name!r} was not found for package {package_name}."
    )


def prepare_release_license_files(platform_build_dir: Path) -> tuple[Path, ...]:
    """Prepare generated license files that should be bundled with the release."""
    python_license_source = python_license_source_path()
    if python_license_source is None:
        raise BuildError(
            "Python license file was not found for the build interpreter. "
            "Cannot prepare a distributable release without Python license notice."
        )

    license_dir = platform_build_dir / "release-licenses"
    license_dir.mkdir(parents=True, exist_ok=True)
    python_license_copy = license_dir / PYTHON_LICENSE_COPY_NAME
    shutil.copyfile(python_license_source, python_license_copy)
    copied_license_files = [python_license_copy]

    for package_name, license_file_name, copy_name in PACKAGE_LICENSE_COPY_TARGETS:
        package_license_source = package_license_source_path(
            package_name,
            license_file_name,
        )
        package_license_copy = license_dir / copy_name
        shutil.copyfile(package_license_source, package_license_copy)
        copied_license_files.append(package_license_copy)

    return tuple(copied_license_files)


def release_notice_data_files(
    license_files: Sequence[Path] = (),
) -> tuple[tuple[Path, str], ...]:
    """Return notice and license files to include in the PyInstaller bundle."""
    return (
        (PROJECT_LICENSE_FILE, "."),
        (THIRD_PARTY_NOTICES_FILE, "."),
        (ABOUT_FILE, "."),
        *((path, LICENSES_DESTINATION) for path in STATIC_LICENSE_FILES),
        *((path, LICENSES_DESTINATION) for path in license_files),
    )


def binary_package_name(current_platform: str) -> str:
    """Return the binary release zip filename for one platform."""
    return f"{APP_NAME}-{APP_VERSION}-{current_platform}.zip"


def source_package_name() -> str:
    """Return the source release zip filename."""
    return f"{APP_NAME}-{APP_VERSION}-source.zip"


def source_package_root_name() -> str:
    """Return the top-level directory name inside the source release zip."""
    return f"{APP_NAME}-{APP_VERSION}-source"


def remove_existing_file(path: Path) -> None:
    """Remove one existing file artifact."""
    if not path.exists():
        return
    if path.is_dir():
        raise BuildError(f"Expected a file path, not a directory: {path}")
    path.unlink()


def remove_existing_source_packages(platform_dist_dir: Path) -> tuple[Path, ...]:
    """Remove obsolete source ZIP artifacts from one platform dist directory."""
    if not platform_dist_dir.exists():
        return ()

    removed_paths: list[Path] = []
    for source_package in sorted(platform_dist_dir.glob(SOURCE_PACKAGE_GLOB)):
        remove_existing_file(source_package)
        removed_paths.append(source_package)
    return tuple(removed_paths)


def package_source_release(platform_dist_dir: Path) -> Path:
    """Create a source release zip from the git HEAD tree."""
    git = shutil.which("git")
    if git is None:
        raise BuildError("git was not found in PATH.")

    platform_dist_dir.mkdir(parents=True, exist_ok=True)
    zip_path = platform_dist_dir / source_package_name()
    remove_existing_file(zip_path)

    archive_root = source_package_root_name()
    command = [
        git,
        "archive",
        "--format=zip",
        f"--prefix={archive_root}/",
        "--output",
        str(zip_path),
        "HEAD",
    ]
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        raise BuildError(f"git archive failed with exit code {result.returncode}.")

    validate_source_package(zip_path, archive_root)
    return zip_path


def package_binary_release(
    release_dir: Path,
    platform_dist_dir: Path,
    current_platform: str,
) -> Path:
    """Create a binary release zip from the validated PyInstaller onedir output."""
    platform_dist_dir.mkdir(parents=True, exist_ok=True)
    zip_path = platform_dist_dir / binary_package_name(current_platform)
    remove_existing_file(zip_path)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for release_file in sorted(path for path in release_dir.rglob("*") if path.is_file()):
            archive_name = release_dir.name / release_file.relative_to(release_dir)
            archive.write(release_file, archive_name.as_posix())

    validate_binary_package(zip_path)
    return zip_path


def validate_source_package(zip_path: Path, archive_root: str) -> None:
    """Validate that a source zip contains mandatory notice files."""
    expected_names = {
        f"{archive_root}/{PROJECT_LICENSE_FILE.name}",
        f"{archive_root}/{THIRD_PARTY_NOTICES_FILE.name}",
        f"{archive_root}/{ABOUT_FILE.name}",
    }
    with zipfile.ZipFile(zip_path) as archive:
        archived_names = set(archive.namelist())

    missing_names = sorted(expected_names - archived_names)
    if missing_names:
        formatted = "\n".join(f"  - {name}" for name in missing_names)
        raise BuildError(f"Source package is missing required file(s):\n{formatted}")


def validate_binary_package(zip_path: Path) -> None:
    """Validate that a binary zip contains mandatory bundled notice files."""
    expected_names = {
        f"{APP_NAME}/{LIB_DIR_NAME}/{PROJECT_LICENSE_FILE.name}",
        f"{APP_NAME}/{LIB_DIR_NAME}/{THIRD_PARTY_NOTICES_FILE.name}",
        f"{APP_NAME}/{LIB_DIR_NAME}/{ABOUT_FILE.name}",
    }
    with zipfile.ZipFile(zip_path) as archive:
        archived_names = set(archive.namelist())

    missing_names = sorted(expected_names - archived_names)
    if missing_names:
        formatted = "\n".join(f"  - {name}" for name in missing_names)
        raise BuildError(f"Binary package is missing required file(s):\n{formatted}")


def package_publish_source_release(publish_dir: Path) -> Path:
    """Create the publish source zip from the git HEAD tree."""
    git = shutil.which("git")
    if git is None:
        raise BuildError("git was not found in PATH.")

    publish_dir.mkdir(parents=True, exist_ok=True)
    zip_path = publish_dir / f"{APP_NAME}-source.zip"
    remove_existing_file(zip_path)

    archive_root = f"{APP_NAME}-source"
    command = [
        git,
        "archive",
        "--format=zip",
        f"--prefix={archive_root}/",
        "--output",
        str(zip_path),
        "HEAD",
    ]
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        raise BuildError(f"git archive failed with exit code {result.returncode}.")

    validate_source_package(zip_path, archive_root)
    return zip_path


def package_publish_binary_release(release_dir: Path, publish_dir: Path) -> Path:
    """Copy the PyInstaller output to Publish and create the app zip."""
    publish_app_dir = publish_dir / APP_NAME
    publish_app_dir.mkdir(parents=True, exist_ok=True)

    remove_existing_file(publish_app_dir / executable_name())
    remove_existing_path(publish_app_dir / LIB_DIR_NAME)
    shutil.copy2(release_dir / executable_name(), publish_app_dir / executable_name())
    shutil.copytree(release_dir / LIB_DIR_NAME, publish_app_dir / LIB_DIR_NAME)

    zip_path = publish_dir / f"{APP_NAME}.zip"
    remove_existing_file(zip_path)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for release_file in sorted(path for path in publish_app_dir.rglob("*") if path.is_file()):
            archive_name = Path(publish_app_dir.name) / release_file.relative_to(publish_app_dir)
            archive.write(release_file, archive_name.as_posix())

    validate_binary_package(zip_path)
    return zip_path


def pyinstaller_command(
    platform_dist_dir: Path,
    platform_build_dir: Path,
    *,
    license_files: Sequence[Path] = (),
) -> list[str]:
    """Build the PyInstaller command for the release."""
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        APP_NAME,
        "--distpath",
        str(platform_dist_dir),
        "--workpath",
        str(platform_build_dir / "work"),
        "--specpath",
        str(platform_build_dir / "spec"),
        "--contents-directory",
        LIB_DIR_NAME,
        "--icon",
        str(ICON_FILE),
    ]
    version_info_file = version_info_file_path(platform_build_dir)
    if platform.system().lower() == "windows":
        command.extend(("--version-file", str(version_info_file)))

    for asset_path in BUNDLED_ASSET_FILES:
        command.extend(("--add-data", add_data_argument(asset_path, "assets")))

    for notice_file, destination in release_notice_data_files(license_files):
        command.extend(("--add-data", add_data_argument(notice_file, destination)))

    for prompt_file in prompt_markdown_files():
        command.extend(
            ("--add-data", add_data_argument(prompt_file, prompt_data_destination(prompt_file)))
        )

    for module_name in OPTIONAL_COLLECT_ALL_MODULES:
        if importlib.util.find_spec(module_name) is not None:
            command.extend(("--collect-all", module_name))

    command.append(str(ENTRYPOINT))
    return command










def command_for_display(command: list[str]) -> str:
    """Return a shell-like command string for logging only."""
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return " ".join(shlex.quote(part) for part in command)


def validate_release_layout(release_dir: Path) -> Path:
    """Validate that PyInstaller produced the requested onedir layout."""
    app_binary = release_dir / executable_name()
    lib_dir = release_dir / LIB_DIR_NAME
    expected_paths = [
        release_dir,
        app_binary,
        lib_dir,
        lib_dir / PROJECT_LICENSE_FILE.name,
        lib_dir / THIRD_PARTY_NOTICES_FILE.name,
        lib_dir / ABOUT_FILE.name,
        lib_dir / "assets" / "app_icon.ico",
        lib_dir / "assets" / "app_icon.png",
        lib_dir / "assets" / "app_icon.svg",
        *(
            lib_dir / LICENSES_DESTINATION / static_license_file.name
            for static_license_file in STATIC_LICENSE_FILES
        ),
        lib_dir / LICENSES_DESTINATION / PYTHON_LICENSE_COPY_NAME,
        *(
            lib_dir / LICENSES_DESTINATION / package_license_name
            for package_license_name in PACKAGE_LICENSE_COPY_NAMES
        ),
        lib_dir / "prompt",
    ]
    expected_paths.extend(
        lib_dir / prompt_file.relative_to(PROJECT_ROOT)
        for prompt_file in prompt_markdown_files()
    )
    missing_paths = [path for path in expected_paths if not path.exists()]
    if missing_paths:
        formatted = "\n".join(f"  - {path}" for path in missing_paths)
        raise BuildError(f"Release layout is incomplete:\n{formatted}")

    internal_dir = release_dir / "_internal"
    if internal_dir.exists():
        raise BuildError(
            f"Unexpected PyInstaller contents directory found: {internal_dir}. "
            f"Expected contents directory: {lib_dir}"
        )
    return app_binary


def open_file_manager(path: Path) -> None:
    """Open the release folder in the OS file manager when possible."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return
        if sys.platform.startswith("linux"):
            subprocess.Popen(
                ["xdg-open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    except OSError as exc:
        print(f"Warning: could not open file manager: {exc}", file=sys.stderr)
        return

    print(f"Warning: opening file manager is not supported on {sys.platform}.", file=sys.stderr)


def build_release(*, publish: bool) -> Path:
    """Build the release and return the app executable path."""
    pyinstaller = pyinstaller_version()
    ensure_required_files()

    current_platform = platform_name()
    platform_dist_dir = DIST_ROOT / current_platform
    platform_build_dir = BUILD_ROOT / current_platform
    release_dir = platform_dist_dir / APP_NAME

    remove_existing_path(release_dir)
    remove_existing_path(platform_build_dir)
    platform_dist_dir.mkdir(parents=True, exist_ok=True)
    platform_build_dir.mkdir(parents=True, exist_ok=True)
    if platform.system().lower() == "windows":
        write_version_info_file(platform_build_dir)

    license_files = prepare_release_license_files(platform_build_dir)
    command = pyinstaller_command(
        platform_dist_dir,
        platform_build_dir,
        license_files=license_files,
    )
    print(f"PyInstaller {pyinstaller}")
    print(f"Build platform: {current_platform}")
    print(f"Release directory: {release_dir}")
    print(f"Command: {command_for_display(command)}")

    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        raise BuildError(f"PyInstaller failed with exit code {result.returncode}.")

    app_binary = validate_release_layout(release_dir)
    if publish:
        publish_dir = PROJECT_ROOT / "Publish"
        source_package = package_publish_source_release(publish_dir)
        binary_package = package_publish_binary_release(release_dir, publish_dir)
        print(f"Source package: {source_package}")
        print(f"Binary package: {binary_package}")
    return app_binary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a PyInstaller onedir release.")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the release directory after a successful build.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Create source and binary archives under the Publish folder.",
    )
    return parser.parse_args(argv)


def main() -> int:
    """Run the release build from the command line."""
    args = parse_args()
    try:
        bootstrap_exit_code = bootstrap_build_environment()
        if bootstrap_exit_code is not None:
            return bootstrap_exit_code
        app_binary = build_release(publish=args.publish)
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Release build completed: {app_binary}")
    if not args.no_open:
        open_file_manager(PROJECT_ROOT / "Publish" if args.publish else app_binary.parent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
