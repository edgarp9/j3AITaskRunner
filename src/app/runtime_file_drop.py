"""AppRuntime file-drop watcher role mixin."""

from __future__ import annotations

import logging
from pathlib import Path
import threading

from domain import FILE_DROP_COMMAND_START_REGISTERED_JOBS
from infra.file_drop import FileDropPollResult, FileDropRequestStore

from .runtime import FileDropCommandRequestedEvent, FileDropIssueEvent

LOGGER = logging.getLogger("app.runtime")


class AppRuntimeFileDropMixin:
    def _start_file_drop_watcher(
        self,
        file_drop_dir: Path | None,
        *,
        poll_interval_seconds: float,
    ) -> None:
        self._file_drop_shutdown_event = threading.Event()
        self._file_drop_thread: threading.Thread | None = None
        self._file_drop_request_store: FileDropRequestStore | None = None
        self._file_drop_poll_interval_seconds = max(float(poll_interval_seconds), 0.1)
        self._file_drop_processed_request_ids: set[str] = set()

        if file_drop_dir is None:
            return

        request_store = FileDropRequestStore(Path(file_drop_dir))
        try:
            request_store.ensure_watch_dir()
        except OSError as exc:
            LOGGER.exception(
                "Failed to create file-drop watch directory. path=%s",
                file_drop_dir,
            )
            self._event_queue.put(
                FileDropIssueEvent(
                    code="watch_dir_create_failed",
                    message="파일 드롭 감시 폴더를 만들지 못했습니다.",
                    detail=str(exc),
                )
            )
            return

        self._file_drop_request_store = request_store
        file_drop_thread = threading.Thread(
            target=self._run_file_drop_watcher,
            name="app-runtime-file-drop",
            daemon=True,
        )
        self._file_drop_thread = file_drop_thread
        file_drop_thread.start()

    def _run_file_drop_watcher(self) -> None:
        shutdown_event = self._file_drop_shutdown_event
        while not shutdown_event.is_set():
            self._poll_file_drop_requests_once()
            shutdown_event.wait(self._file_drop_poll_interval_seconds)

    def _poll_file_drop_requests_once(self) -> None:
        request_store = self._file_drop_request_store
        if request_store is None or self._runtime_action_shutdown_requested:
            return

        try:
            poll_result = request_store.poll()
        except Exception:
            LOGGER.exception("Unexpected file-drop polling failure.")
            self._event_queue.put(
                FileDropIssueEvent(
                    code="poll_failed",
                    message="파일 드롭 요청을 확인하지 못했습니다.",
                )
            )
            return

        self._enqueue_file_drop_poll_result(poll_result)

    def _enqueue_file_drop_poll_result(self, poll_result: FileDropPollResult) -> None:
        if self._runtime_action_shutdown_requested:
            return

        for accepted_file in poll_result.accepted_files:
            request = accepted_file.request
            if request.request_id in self._file_drop_processed_request_ids:
                LOGGER.info(
                    "Skipping already processed file-drop request. request_id=%s",
                    request.request_id,
                )
                continue
            self._file_drop_processed_request_ids.add(request.request_id)
            for command in request.commands:
                if command.type != FILE_DROP_COMMAND_START_REGISTERED_JOBS:
                    LOGGER.warning(
                        "Ignoring unsupported validated file-drop command. "
                        "request_id=%s command_type=%s",
                        request.request_id,
                        command.type,
                    )
                    self._event_queue.put(
                        FileDropIssueEvent(
                            code="unknown_command_type",
                            message="지원하지 않는 파일 드롭 command type입니다.",
                            detail=command.type,
                        )
                    )
                    continue

                self._event_queue.put(
                    FileDropCommandRequestedEvent(
                        request_id=request.request_id,
                        command_type=command.type,
                    )
                )

        for issue in poll_result.issues:
            self._event_queue.put(
                FileDropIssueEvent(
                    code=issue.code,
                    message=issue.message,
                    detail=issue.detail,
                )
            )

    def _stop_file_drop_watcher(self) -> None:
        shutdown_event = getattr(self, "_file_drop_shutdown_event", None)
        if shutdown_event is not None:
            shutdown_event.set()

    def _file_drop_watcher_is_alive(self) -> bool:
        file_drop_thread = getattr(self, "_file_drop_thread", None)
        return bool(file_drop_thread is not None and file_drop_thread.is_alive())
