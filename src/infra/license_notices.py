"""Third-party license notice loading helpers."""

from __future__ import annotations

from pathlib import Path
import sys

THIRD_PARTY_NOTICES_FILE_NAME = "THIRD_PARTY_NOTICES.txt"
PROJECT_LICENSE_FILE_NAME = "LICENSE"
ABOUT_FILE_NAME = "about.txt"
HIDDEN_THIRD_PARTY_NOTICE_SECTIONS = frozenset(
    {
        "Release Checklist",
    }
)


def _visible_third_party_notice_text(notice_text: str) -> str:
    """Remove internal release-management sections from user-facing notices."""
    visible_lines: list[str] = []
    skip_section = False

    for line in notice_text.splitlines(keepends=True):
        if line.startswith("## "):
            heading = line[3:].strip()
            skip_section = heading in HIDDEN_THIRD_PARTY_NOTICE_SECTIONS
            if skip_section:
                continue

        if not skip_section:
            visible_lines.append(line)

    return "".join(visible_lines)


def third_party_notice_candidate_paths() -> tuple[Path, ...]:
    """Return possible third-party notice file locations."""
    paths: list[Path] = []
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        paths.append(Path(bundled_root) / THIRD_PARTY_NOTICES_FILE_NAME)

    project_root = Path(__file__).resolve().parents[1]
    paths.append(project_root / THIRD_PARTY_NOTICES_FILE_NAME)
    return tuple(dict.fromkeys(paths))


def about_notice_candidate_paths() -> tuple[Path, ...]:
    """Return possible About notice file locations."""
    paths: list[Path] = []
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        paths.append(Path(bundled_root) / ABOUT_FILE_NAME)

    project_root = Path(__file__).resolve().parents[1]
    paths.append(project_root / ABOUT_FILE_NAME)
    return tuple(dict.fromkeys(paths))


def project_license_candidate_paths() -> tuple[Path, ...]:
    """Return possible project GPL license file locations."""
    paths: list[Path] = []
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        paths.append(Path(bundled_root) / PROJECT_LICENSE_FILE_NAME)

    project_root = Path(__file__).resolve().parents[1]
    paths.append(project_root / PROJECT_LICENSE_FILE_NAME)
    return tuple(dict.fromkeys(paths))


def load_third_party_notices() -> str:
    """Load the bundled or source third-party notice text."""
    checked_paths = third_party_notice_candidate_paths()
    for notice_path in checked_paths:
        if notice_path.is_file():
            notice_text = notice_path.read_text(encoding="utf-8")
            return _visible_third_party_notice_text(notice_text)

    formatted_paths = ", ".join(str(path) for path in checked_paths)
    raise FileNotFoundError(f"Third-party notices file was not found: {formatted_paths}")


def load_about_notice() -> str:
    """Load the bundled or source About notice text."""
    checked_paths = about_notice_candidate_paths()
    for about_path in checked_paths:
        if about_path.is_file():
            return about_path.read_text(encoding="utf-8").strip()

    formatted_paths = ", ".join(str(path) for path in checked_paths)
    raise FileNotFoundError(f"About notice file was not found: {formatted_paths}")


def load_project_license_notice() -> str:
    """Load the bundled or source project GPL license text."""
    checked_paths = project_license_candidate_paths()
    for license_path in checked_paths:
        if license_path.is_file():
            license_text = license_path.read_text(encoding="utf-8").strip()
            return f"# j3AITaskRunner License\n\n{license_text}"

    formatted_paths = ", ".join(str(path) for path in checked_paths)
    raise FileNotFoundError(f"Project license file was not found: {formatted_paths}")


def load_license_notices() -> str:
    """Load the app license followed by third-party notices."""
    return f"{load_project_license_notice()}\n\n{load_third_party_notices()}"
