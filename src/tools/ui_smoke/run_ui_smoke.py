"""Standard UI smoke test driver for local runs and CI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import NoReturn

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from infra.repository import PERSISTENCE_FILE_NAME
from tools.ui_smoke.app_process import has_fatal_output
from tools.ui_smoke.run_ui_smoke_validation import (
    EXPECTED_ACTION_IDS,
    _action_items,
    _validate_persistence_file,
    _validate_report,
)


DEFAULT_TIMEOUT_SECONDS = 30.0
PROCESS_TIMEOUT_PADDING_SECONDS = 10.0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    timeout_seconds = _timeout_seconds(args.timeout)
    keep_temp = args.keep_temp or _truthy_env("KEEP_UI_SMOKE_TEMP")
    artifacts_dir = repo_root / "tools" / "ui_smoke" / "artifacts" / "latest"
    temp_root = Path(tempfile.mkdtemp(prefix="j3aitaskrunner-ui-smoke-"))
    success = False
    started_at = time.monotonic()

    try:
        context = _prepare_context(repo_root, temp_root)
        result = _run_app_process(
            repo_root=repo_root,
            context=context,
            timeout_seconds=timeout_seconds,
        )
        report = _validate_result(context=context, exit_code=result.returncode)
        success = True
        _print_success_summary(
            report=report,
            context=context,
            elapsed_seconds=time.monotonic() - started_at,
            verbose=args.verbose,
        )
        return 0
    except Exception as exc:
        _copy_diagnostics(temp_root, artifacts_dir)
        _print_failure_summary(
            error=exc,
            temp_root=temp_root,
            artifacts_dir=artifacts_dir,
            elapsed_seconds=time.monotonic() - started_at,
            verbose=args.verbose,
        )
        return 1
    finally:
        if keep_temp or not success:
            print(f"UI smoke temp kept at {temp_root}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the j3AITaskRunner UI smoke test.")
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary profile/data directory after the run.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="UI scenario timeout in seconds. Defaults to UI_SMOKE_TIMEOUT_SECONDS or 30.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print passed steps and raw app stdout/stderr logs.",
    )
    return parser.parse_args(argv)


def _timeout_seconds(value: float | None) -> float:
    if value is not None:
        if value <= 0:
            raise RuntimeError("--timeout must be greater than zero.")
        return value
    raw_value = os.environ.get("UI_SMOKE_TIMEOUT_SECONDS")
    if not raw_value:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        parsed = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid UI_SMOKE_TIMEOUT_SECONDS value: {raw_value}") from exc
    if parsed <= 0:
        raise RuntimeError("UI_SMOKE_TIMEOUT_SECONDS must be greater than zero.")
    return parsed


def _prepare_context(repo_root: Path, temp_root: Path) -> dict[str, Path]:
    paths = {
        "temp_root": temp_root,
        "home": temp_root / "home",
        "appdata": temp_root / "AppData" / "Roaming",
        "localappdata": temp_root / "AppData" / "Local",
        "xdg_config": temp_root / "xdg-config",
        "xdg_data": temp_root / "xdg-data",
        "xdg_cache": temp_root / "xdg-cache",
        "tmp": temp_root / "tmp",
        "storage": temp_root / "storage",
        "workspace": temp_root / "workspace",
        "logs": temp_root / "logs",
        "report": temp_root / "logs" / "ui-smoke-report.json",
        "stdout": temp_root / "logs" / "app-stdout.log",
        "stderr": temp_root / "logs" / "app-stderr.log",
        "fake_agent": temp_root / _fake_agent_name(),
    }
    for path in paths.values():
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)
    _write_fake_agent(paths["fake_agent"])
    _write_initial_persistence(paths["storage"], paths["fake_agent"])
    return paths


def _fake_agent_name() -> str:
    return "fake-codex.cmd" if platform.system().lower() == "windows" else "fake-codex.sh"


def _write_fake_agent(path: Path) -> None:
    helper_path = path.with_suffix(".py")
    helper_path.write_text(
        "\n".join(
            (
                "from __future__ import annotations",
                "",
                "import json",
                "from pathlib import Path",
                "import sys",
                "",
                "",
                "def main() -> int:",
                "    args = sys.argv[1:]",
                "    if '--version' in args:",
                "        print('ui smoke fake agent')",
                "        return 0",
                "",
                "    output_path = _option_value(args, '-o')",
                "    prompt = sys.stdin.read()",
                "    if output_path:",
                "        response = _response_text(prompt)",
                "        Path(output_path).parent.mkdir(parents=True, exist_ok=True)",
                "        Path(output_path).write_text(response, encoding='utf-8')",
                "",
                "    print(json.dumps({'type': 'thread.started', 'thread_id': 'ui-smoke-thread'}), flush=True)",
                "    print(json.dumps({'type': 'turn.completed'}), flush=True)",
                "    return 0",
                "",
                "",
                "def _option_value(args: list[str], option: str) -> str | None:",
                "    for index, value in enumerate(args[:-1]):",
                "        if value == option:",
                "            return args[index + 1]",
                "    return None",
                "",
                "",
                "def _response_text(prompt: str) -> str:",
                "    normalized_prompt = prompt.encode('utf-8', errors='replace').decode('utf-8')",
                "    preview = ' '.join(normalized_prompt.split())[:80] or 'empty prompt'",
                "    return f'UI smoke fake response for: {preview}'",
                "",
                "",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
                "",
            )
        ),
        encoding="utf-8",
    )
    if platform.system().lower() == "windows":
        path.write_text(
            f'@echo off\nset PYTHONUTF8=1\n"{sys.executable}" "%~dp0{helper_path.name}" %*\n',
            encoding="utf-8",
        )
        return
    path.write_text(
        "#!/usr/bin/env sh\n"
        'SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
        f'PYTHONUTF8=1 exec "{sys.executable}" "$SCRIPT_DIR/{helper_path.name}" "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_initial_persistence(storage_dir: Path, fake_agent_path: Path) -> None:
    payload = {
        "settings": {
            "agent_provider": "codex",
            "executable_path": str(fake_agent_path),
            "executable_paths": {"codex": str(fake_agent_path)},
            "file_logging_enabled": False,
            "ui_language": "en",
        },
        "saved_workspaces": [],
    }
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / PERSISTENCE_FILE_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _run_app_process(
    *,
    repo_root: Path,
    context: dict[str, Path],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[None]:
    command = [
        sys.executable,
        str(repo_root / "main.py"),
        "--data-dir",
        str(context["storage"]),
        "--ui-smoke-report",
        str(context["report"]),
        "--ui-smoke-timeout",
        str(timeout_seconds),
        str(context["workspace"]),
    ]
    env = _isolated_environment(context)
    with (
        context["stdout"].open("w", encoding="utf-8") as stdout,
        context["stderr"].open("w", encoding="utf-8") as stderr,
    ):
        try:
            return subprocess.run(
                command,
                cwd=repo_root,
                env=env,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout_seconds + PROCESS_TIMEOUT_PADDING_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"App process timed out after {exc.timeout} seconds."
            ) from exc


def _isolated_environment(context: dict[str, Path]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(context["home"]),
            "USERPROFILE": str(context["home"]),
            "APPDATA": str(context["appdata"]),
            "LOCALAPPDATA": str(context["localappdata"]),
            "XDG_CONFIG_HOME": str(context["xdg_config"]),
            "XDG_DATA_HOME": str(context["xdg_data"]),
            "XDG_CACHE_HOME": str(context["xdg_cache"]),
            "TMPDIR": str(context["tmp"]),
            "TMP": str(context["tmp"]),
            "TEMP": str(context["tmp"]),
            "PYTHONUNBUFFERED": "1",
            "J3AITASKRUNNER_UI_SMOKE": "1",
        }
    )
    return env


def _validate_result(*, context: dict[str, Path], exit_code: int) -> dict[str, object]:
    stdout_text = _read_text(context["stdout"])
    stderr_text = _read_text(context["stderr"])
    combined_output = f"{stdout_text}\n{stderr_text}"
    if has_fatal_output(combined_output):
        _fail(f"fatal output found in app logs: {combined_output[-1200:]}")
    if exit_code != 0:
        _fail(f"app process exited with code {exit_code}")
    if not context["report"].is_file():
        _fail(f"smoke report was not created: {context['report']}")

    report = _load_json_report(context["report"])
    _validate_report(report)
    _validate_persistence_file(context)
    return report


def _print_success_summary(
    *,
    report: dict[str, object],
    context: dict[str, Path],
    elapsed_seconds: float,
    verbose: bool,
) -> None:
    counts = _action_counts(report)
    print(
        "UI smoke passed: "
        f"{counts['passed']} passed, {counts['skipped']} skipped, "
        f"{counts['failed']} failed in {elapsed_seconds:.1f}s."
    )
    if not verbose:
        return

    print("UI smoke steps:")
    _print_action_details(report, stream=sys.stdout)
    _print_log_file("app stdout", context["stdout"], stream=sys.stdout)
    _print_log_file("app stderr", context["stderr"], stream=sys.stdout)


def _print_failure_summary(
    *,
    error: Exception,
    temp_root: Path,
    artifacts_dir: Path,
    elapsed_seconds: float,
    verbose: bool,
) -> None:
    report = _try_load_json_report(temp_root / "logs" / "ui-smoke-report.json")
    if report:
        last_action = report.get("last_action") or "unknown"
        print(f"UI smoke failed at {last_action}: {error}", file=sys.stderr)
    else:
        print(f"UI smoke failed: {error}", file=sys.stderr)
    print(f"UI smoke elapsed: {elapsed_seconds:.1f}s", file=sys.stderr)
    print(f"UI smoke diagnostics copied to {artifacts_dir}", file=sys.stderr)
    if not verbose:
        return

    if report:
        print("UI smoke steps:", file=sys.stderr)
        _print_action_details(report, stream=sys.stderr)
    _print_log_file(
        "app stdout",
        temp_root / "logs" / "app-stdout.log",
        stream=sys.stderr,
    )
    _print_log_file(
        "app stderr",
        temp_root / "logs" / "app-stderr.log",
        stream=sys.stderr,
    )


def _action_counts(report: dict[str, object]) -> dict[str, int]:
    counts = {"passed": 0, "skipped": 0, "failed": 0}
    for action in _action_items(report):
        status = action.get("status")
        if status == "passed":
            counts["passed"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        elif status == "failed":
            counts["failed"] += 1
    return counts


def _print_action_details(
    report: dict[str, object],
    *,
    stream: object,
) -> None:
    for action in _action_items(report):
        action_id = action.get("action_id", "unknown")
        status = action.get("status", "unknown")
        print(f"  {status}: {action_id}", file=stream)
        details = {
            key: value
            for key, value in action.items()
            if key not in {"action_id", "status"}
        }
        if details:
            print(
                "    "
                + json.dumps(
                    details,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
                file=stream,
            )


def _print_log_file(label: str, path: Path, *, stream: object) -> None:
    text = _read_text(path)
    print(f"{label}: {path}", file=stream)
    if text.strip():
        print(text.rstrip(), file=stream)
    else:
        print("(empty)", file=stream)


def _try_load_json_report(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if isinstance(report, dict):
        return report
    return None


def _load_json_report(path: Path) -> dict[str, object]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"smoke report is not valid JSON: {path}: {exc}")
    if not isinstance(report, dict):
        _fail("smoke report root must be a JSON object.")
    return report


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _copy_diagnostics(temp_root: Path, artifacts_dir: Path) -> None:
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(temp_root, artifacts_dir)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _fail(message: str) -> NoReturn:
    raise RuntimeError(message)


if __name__ == "__main__":
    raise SystemExit(main())
