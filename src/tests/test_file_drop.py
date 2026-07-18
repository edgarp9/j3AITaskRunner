from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from domain import (
    FILE_DROP_COMMAND_START_REGISTERED_JOBS,
    FILE_DROP_SCHEMA,
    FileDropRequestError,
    parse_file_drop_request_text,
)
from infra.file_drop import FileDropRequestStore


def _request_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": FILE_DROP_SCHEMA,
        "request_id": "1234567890",
        "commands": [{"type": FILE_DROP_COMMAND_START_REGISTERED_JOBS}],
    }
    payload.update(overrides)
    return payload


class FileDropRequestParserTests(unittest.TestCase):
    def test_parses_start_registered_jobs_request(self) -> None:
        request = parse_file_drop_request_text(json.dumps(_request_payload()))

        self.assertEqual(FILE_DROP_SCHEMA, request.schema)
        self.assertEqual("1234567890", request.request_id)
        self.assertEqual(
            (FILE_DROP_COMMAND_START_REGISTERED_JOBS,),
            tuple(command.type for command in request.commands),
        )

    def test_rejects_invalid_schema(self) -> None:
        with self.assertRaises(FileDropRequestError) as captured:
            parse_file_drop_request_text(
                json.dumps(_request_payload(schema="other.schema"))
            )

        self.assertEqual("invalid_schema", captured.exception.code)

    def test_rejects_invalid_request_id(self) -> None:
        invalid_request_ids = ("123456789", "12345678901", "abcdefghij", 1234567890)
        for request_id in invalid_request_ids:
            with self.subTest(request_id=request_id):
                with self.assertRaises(FileDropRequestError) as captured:
                    parse_file_drop_request_text(
                        json.dumps(_request_payload(request_id=request_id))
                    )

                self.assertEqual("invalid_request_id", captured.exception.code)

    def test_rejects_unknown_command_type(self) -> None:
        with self.assertRaises(FileDropRequestError) as captured:
            parse_file_drop_request_text(
                json.dumps(_request_payload(commands=[{"type": "unknown"}]))
            )

        self.assertEqual("unknown_command_type", captured.exception.code)
        self.assertEqual("unknown", captured.exception.detail)

    def test_rejects_broken_json(self) -> None:
        with self.assertRaises(FileDropRequestError) as captured:
            parse_file_drop_request_text('{"schema": ')

        self.assertEqual("invalid_json", captured.exception.code)


class FileDropRequestStoreTests(unittest.TestCase):
    def test_poll_reads_deletes_and_parses_request_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            watch_dir = Path(temp_dir) / "watch"
            store = FileDropRequestStore(watch_dir)
            store.ensure_watch_dir()
            request_path = watch_dir / "request.j3aitask.json"
            request_path.write_text(json.dumps(_request_payload()), encoding="utf-8")

            result = store.poll()

            self.assertFalse(request_path.exists())
            self.assertEqual((), result.issues)
            self.assertEqual(1, len(result.accepted_files))
            self.assertEqual("1234567890", result.accepted_files[0].request.request_id)

    def test_poll_reports_broken_json_and_continues_after_deleting_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            watch_dir = Path(temp_dir) / "watch"
            store = FileDropRequestStore(watch_dir)
            store.ensure_watch_dir()
            request_path = watch_dir / "broken.j3aitask.json"
            request_path.write_text('{"schema": ', encoding="utf-8")

            with self.assertLogs("infra.file_drop", level="WARNING") as logs:
                result = store.poll()

            self.assertFalse(request_path.exists())
            self.assertEqual((), result.accepted_files)
            self.assertEqual(("invalid_json",), tuple(issue.code for issue in result.issues))
            self.assertIn("Invalid file-drop request.", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
