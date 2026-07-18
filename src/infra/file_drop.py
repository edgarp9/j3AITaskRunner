"""Filesystem access for file-drop trigger requests."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from domain import FileDropRequest, FileDropRequestError, parse_file_drop_request_text

LOGGER = logging.getLogger(__name__)
FILE_DROP_REQUEST_GLOB = "*.j3aitask.json"


@dataclass(slots=True, frozen=True)
class FileDropAcceptedFile:
    """One successfully parsed request file."""

    path: Path
    request: FileDropRequest


@dataclass(slots=True, frozen=True)
class FileDropIssue:
    """One non-fatal issue found while polling the watch directory."""

    path: Path | None
    code: str
    message: str
    detail: str = ""


@dataclass(slots=True, frozen=True)
class FileDropPollResult:
    """Result of one file-drop directory scan."""

    accepted_files: tuple[FileDropAcceptedFile, ...] = ()
    issues: tuple[FileDropIssue, ...] = ()


class FileDropRequestStore:
    """Read, delete, and parse request files from a watch directory."""

    def __init__(self, watch_dir: Path) -> None:
        self.watch_dir = Path(watch_dir)

    def ensure_watch_dir(self) -> None:
        """Create the watch directory if it does not exist."""
        self.watch_dir.mkdir(parents=True, exist_ok=True)

    def poll(self) -> FileDropPollResult:
        """Scan the watch directory once for request files."""
        try:
            request_paths = tuple(sorted(self.watch_dir.glob(FILE_DROP_REQUEST_GLOB)))
        except OSError as exc:
            LOGGER.exception("Failed to scan file-drop watch directory.")
            return FileDropPollResult(
                issues=(
                    FileDropIssue(
                        path=self.watch_dir,
                        code="scan_failed",
                        message="파일 드롭 감시 폴더를 읽지 못했습니다.",
                        detail=str(exc),
                    ),
                )
            )

        accepted_files: list[FileDropAcceptedFile] = []
        issues: list[FileDropIssue] = []
        for request_path in request_paths:
            accepted_file, file_issues = self._read_delete_parse(request_path)
            if accepted_file is not None:
                accepted_files.append(accepted_file)
            issues.extend(file_issues)

        return FileDropPollResult(
            accepted_files=tuple(accepted_files),
            issues=tuple(issues),
        )

    def _read_delete_parse(
        self,
        request_path: Path,
    ) -> tuple[FileDropAcceptedFile | None, tuple[FileDropIssue, ...]]:
        try:
            payload_text = request_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            LOGGER.warning(
                "Failed to read file-drop request file. path=%s",
                request_path,
                exc_info=True,
            )
            return (
                None,
                (
                    FileDropIssue(
                        path=request_path,
                        code="read_failed",
                        message="파일 드롭 요청 파일을 읽지 못했습니다.",
                        detail=str(exc),
                    ),
                ),
            )

        issues: list[FileDropIssue] = []
        try:
            request_path.unlink()
        except OSError as exc:
            LOGGER.warning(
                "Failed to delete file-drop request file after reading. path=%s",
                request_path,
                exc_info=True,
            )
            issues.append(
                FileDropIssue(
                    path=request_path,
                    code="delete_failed",
                    message="파일 드롭 요청 파일을 삭제하지 못했습니다.",
                    detail=str(exc),
                )
            )

        try:
            request = parse_file_drop_request_text(payload_text)
        except FileDropRequestError as exc:
            LOGGER.warning(
                "Invalid file-drop request. path=%s code=%s detail=%s",
                request_path,
                exc.code,
                exc.detail,
            )
            issues.append(
                FileDropIssue(
                    path=request_path,
                    code=exc.code,
                    message=str(exc),
                    detail=exc.detail,
                )
            )
            return None, tuple(issues)

        return FileDropAcceptedFile(path=request_path, request=request), tuple(issues)
