# 빌드/테스트 문서

실행 명령, 빠른 검증, 전체 검증, UI smoke, 테스트 선택 기준을 확인할 때 읽는다. 릴리즈 빌드는 [`release.md`](release.md)를 따른다.

## 실행

Windows:

```powershell
py main.py
python main.py
```

Linux/macOS:

```bash
python3 main.py
./main.py
```

워크스페이스 시작 인자와 격리 데이터 경로:

```bash
python main.py <workspace_path> [<workspace_path> ...]
python main.py --data-dir <path>
```

## Python 기준

- CI 기준 Python은 3.12다.
- 소스 실행과 기본 unittest는 표준 라이브러리와 Tkinter를 기준으로 한다.
- 로컬 Python 버전이 다르면 실패를 CI와 같은 조건의 실패로 단정하지 말고 Python 3.12 또는 원격 CI에서 재확인한다.
- 선택적 개발/빌드 의존성은 [`../requirements-dev.txt`](../requirements-dev.txt)에 둔다.

## 빠른 검증

문서만 수정한 경우 코드 테스트는 생략할 수 있다. 대신 `docs` Markdown 파일 목록, 제목 구조, 상대 링크 대상 파일 존재 여부를 확인한다.

```bash
python -m compileall main.py app domain infra ui tests tools/ui_smoke
python -m unittest tests.test_static_quality
```

특정 기능 변경 시 관련 테스트를 직접 실행한다.

| 영역 | 우선 테스트 |
| --- | --- |
| Provider 명령/stdout/완료 판정 | `tests/test_process_runner*.py`, `tests/test_agent_cli_*.py` |
| Timeout/cancel/process tree | `tests/test_process_runner*.py`, `tests/test_timeout_smoke.py`, `tests/test_controller*.py`, `tests/test_execution_worker.py` |
| 설정 저장/호환성/provider 경로/queue mode | `tests/test_persistence*.py`, `tests/test_main_window_settings_dialog.py` |
| 작업 큐/shared/바로실행/프리셋 pending | `tests/test_controller*.py`, `tests/test_app_runtime*.py`, `tests/test_scheduler*.py`, `tests/test_preset_flow*.py` |
| 세션 종료 훅 | `tests/test_session_exit_hook.py`, `tests/test_main_window_dialogs.py`, `tests/test_app_runtime_managers.py` |
| Bulk import | `tests/test_bulk_import.py` |
| MainWindow/UI | `tests/test_main_window*.py`, `tests/_main_window_helpers*.py` |
| DPI/windowing/resources/messages | `tests/test_ui_dpi.py`, `tests/test_windows_dpi.py`, `tests/test_ui_windowing.py`, `tests/test_ui_resources.py`, `tests/test_messages.py` |
| 릴리즈/라이선스/앱 진입점 | `tests/test_build_release.py`, `tests/test_license_notices.py`, `tests/test_main.py` |

## 전체 검증

```bash
python -m compileall main.py app domain infra ui tests tools/ui_smoke
python -m unittest
```

선택 검증:

```bash
python -m pytest
ruff check .
```

`pytest`와 `ruff`는 기본 의존성이 아니므로 미설치 자체를 실패로 보지 않는다.

## UI smoke

UI smoke는 실제 `main.py` Tkinter 앱 프로세스를 실행하는 느린 통합 검증이다. 실제 AI 실행기는 호출하지 않고 fake Codex executable만 사용하며, 작업 등록과 fake 큐 실행/이력 렌더링 경로를 확인한다. 자세한 wrapper 사용법과 artifact 해석은 [`../tools/ui_smoke/README.md`](../tools/ui_smoke/README.md)를 따른다.

```powershell
pwsh -File tools/ui_smoke/run.ps1
```

```bash
bash tools/ui_smoke/run.sh
```

verbose 실행:

```powershell
pwsh -File tools/ui_smoke/run.ps1 -Verbose
```

```bash
bash tools/ui_smoke/run.sh --verbose
```

기본 실행은 성공 시 짧은 요약만 출력하고, 실패 시 실패 단계와 진단 폴더 경로를 출력한다. 성공 단계별 세부 정보와 앱 stdout/stderr 로그가 필요하면 verbose 실행을 사용한다.

실제 wrapper 실행 대상:

- Tkinter 화면, 다이얼로그, workspace/session/job 등록 경로, persistence 저장 경로를 바꾼 경우.
- UI smoke wrapper나 report/action/window diagnostics 계약을 바꾼 경우.
- CI UI smoke 실패 재현 또는 릴리즈 전 실제 앱 실행 증거가 필요한 경우.

화면 환경이 없거나 wrapper 내부 계약만 바꿨다면 다음만 실행할 수 있다.

```bash
python -B -m unittest tests.test_ui_smoke_contract
```

실제 provider/AI CLI 호출 검증은 기본 검증에 포함하지 않는다. `J3AITASKRUNNER_RUN_REAL_AGENT_SMOKE=1` 같은 명시 opt-in 조건에서만 실행한다.
