"""Build a PyInstaller onedir release for j3AITaskRunner."""

from __future__ import annotations

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
SOURCE_PACKAGE_NAME = f"{APP_NAME}-{APP_VERSION}-source.zip"
SOURCE_PACKAGE_ROOT_NAME = f"{APP_NAME}-{APP_VERSION}-source"

SOURCE_PACKAGE_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".build-venv",
        ".eggs",
        ".git",
        ".hypothesis",
        ".idea",
        ".j3aitaskrunner",
        ".my",
        ".mypy_cache",
        ".nox",
        ".pyre",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".vscode",
        "__pycache__",
        "build",
        "data",
        "dist",
        "env",
        "ENV",
        "htmlcov",
        "lib",
        "log",
        "pip-wheel-metadata",
        "site-packages",
        "tmp_validation",
        "venv",
    }
)
SOURCE_PACKAGE_EXCLUDED_FILE_NAMES = frozenset(
    {
        ".coverage",
        ".DS_Store",
        ".directory",
        "Desktop.ini",
        "Thumbs.db",
    }
)
SOURCE_PACKAGE_EXCLUDED_SUFFIXES = (
    ".bak",
    ".log",
    ".pyd",
    ".pyc",
    ".pyo",
    ".swp",
    ".swo",
    ".tmp",
)

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


def remove_existing_file(path: Path) -> None:
    """Remove one existing file artifact."""
    if not path.exists():
        return
    if path.is_dir():
        raise BuildError(f"Expected a file path, not a directory: {path}")
    path.unlink()


def _is_source_package_file_excluded(path: Path) -> bool:
    relative_path = path.relative_to(PROJECT_ROOT)
    for part in relative_path.parts:
        if _is_source_package_dir_name_excluded(part):
            return True

    file_name = relative_path.name
    if file_name in SOURCE_PACKAGE_EXCLUDED_FILE_NAMES:
        return True
    if file_name.endswith("~"):
        return True
    if file_name.startswith((".coverage.", ".fuse_hidden", ".nfs", ".xsession-errors")):
        return True
    return file_name.endswith(SOURCE_PACKAGE_EXCLUDED_SUFFIXES)


def _is_source_package_dir_name_excluded(name: str) -> bool:
    return (
        name in SOURCE_PACKAGE_EXCLUDED_DIR_NAMES
        or name.endswith(".egg-info")
        or name.startswith(".Trash-")
    )


def source_package_files() -> tuple[Path, ...]:
    """Return project files to include in the source release package."""
    source_files: list[Path] = []
    for current_root, dir_names, file_names in os.walk(PROJECT_ROOT):
        dir_names[:] = [
            name for name in dir_names if not _is_source_package_dir_name_excluded(name)
        ]
        current_path = Path(current_root)
        for file_name in file_names:
            path = current_path / file_name
            if not _is_source_package_file_excluded(path):
                source_files.append(path)
    return tuple(sorted(source_files))


def package_source_release(platform_dist_dir: Path) -> Path:
    """Create the same-version source zip required for binary distribution."""
    platform_dist_dir.mkdir(parents=True, exist_ok=True)
    zip_path = platform_dist_dir / SOURCE_PACKAGE_NAME
    remove_existing_file(zip_path)

    source_files = source_package_files()
    required_files = (PROJECT_LICENSE_FILE, THIRD_PARTY_NOTICES_FILE, ABOUT_FILE)
    missing_required_files = [path for path in required_files if path not in source_files]
    if missing_required_files:
        formatted = "\n".join(f"  - {path}" for path in missing_required_files)
        raise BuildError(f"Source package is missing required notice file(s):\n{formatted}")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source_file in source_files:
            archive_name = Path(SOURCE_PACKAGE_ROOT_NAME) / source_file.relative_to(
                PROJECT_ROOT
            )
            archive.write(source_file, archive_name.as_posix())

    validate_source_package(zip_path)
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


def validate_source_package(zip_path: Path) -> None:
    """Validate that a source zip contains mandatory release notice files."""
    expected_names = {
        f"{SOURCE_PACKAGE_ROOT_NAME}/{PROJECT_LICENSE_FILE.name}",
        f"{SOURCE_PACKAGE_ROOT_NAME}/{THIRD_PARTY_NOTICES_FILE.name}",
        f"{SOURCE_PACKAGE_ROOT_NAME}/{ABOUT_FILE.name}",
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


def version_info_file_path(platform_build_dir: Path) -> Path:
    """Return the generated Windows version-info file path for one build."""
    return platform_build_dir / VERSION_INFO_FILE_NAME


def write_version_info_file(platform_build_dir: Path) -> Path:
    """Generate a PyInstaller Windows version-info file from the app version."""
    version_info_file = version_info_file_path(platform_build_dir)
    version_info_file.parent.mkdir(parents=True, exist_ok=True)
    version_info_file.write_text(_format_version_info_file(), encoding="utf-8")
    return version_info_file


def _format_version_info_file() -> str:
    version_tuple = _windows_version_tuple(APP_VERSION)
    return f"""# UTF-8
#
# Generated by build_release.py from app.version.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'j3'),
          StringStruct('FileDescription', '{APP_NAME}'),
          StringStruct('FileVersion', '{APP_VERSION}'),
          StringStruct('InternalName', '{APP_NAME}'),
          StringStruct('OriginalFilename', '{APP_NAME}.exe'),
          StringStruct('ProductName', '{APP_NAME}'),
          StringStruct('ProductVersion', '{APP_VERSION}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def _windows_version_tuple(version_text: str) -> tuple[int, int, int, int]:
    numeric_parts = []
    for part in version_text.split("."):
        match = re.match(r"(\d+)", part)
        if match is None:
            break
        numeric_parts.append(int(match.group(1)))
    while len(numeric_parts) < 4:
        numeric_parts.append(0)
    return tuple(numeric_parts[:4])


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


def build_release() -> Path:
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
    source_package = package_source_release(platform_dist_dir)
    binary_package = package_binary_release(
        release_dir,
        platform_dist_dir,
        current_platform,
    )
    print(f"Source package: {source_package}")
    print(f"Binary package: {binary_package}")
    return app_binary


def main() -> int:
    """Run the release build from the command line."""
    try:
        bootstrap_exit_code = bootstrap_build_environment()
        if bootstrap_exit_code is not None:
            return bootstrap_exit_code
        app_binary = build_release()
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Release build completed: {app_binary}")
    open_file_manager(app_binary.parent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
