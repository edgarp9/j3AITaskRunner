# UI Smoke Test

## 문서 역할

이 문서는 UI smoke wrapper의 사용법, 자동 시나리오, 격리 환경, 실패 artifact 해석만 다룬다. 빠른 검증, 전체 검증, UI smoke 실행 여부, 릴리즈 검증의 선택 기준은 [`../../docs/build-and-test.md`](../../docs/build-and-test.md)를 따른다.

## 목적

`j3AITaskRunner`의 실제 `main.py` Tkinter 앱 프로세스를 실행해 핵심 UI 경로가 살아 있는지 확인한다. 자동 smoke는 실제 AI 실행기를 호출하지 않고 임시 HOME/profile/config/data/cache/temp 디렉터리, 임시 `--data-dir`, fake Codex executable만 사용한다. fake executable은 유효한 Codex JSONL과 마지막 응답 파일을 만들어 큐 실행, 진행 로그, 이력 렌더링까지 검증한다.

## 전제 조건

- Python 3.12 기준으로 실행한다.
- Tkinter가 동작하는 로컬 GUI 세션 또는 Linux `xvfb-run` 환경이 필요하다.
- 실제 provider/AI CLI 설정은 사용하지 않는다. wrapper가 임시 fake Codex executable과 임시 `--data-dir`를 준비한다.

## 자동 smoke

표준 진입점은 다음 두 wrapper다. 직접 `run_ui_smoke.py`를 호출하지 말고 OS별 wrapper를 사용한다.

Windows:

```powershell
pwsh -File tools/ui_smoke/run.ps1
```

Linux/macOS:

```bash
bash tools/ui_smoke/run.sh
```

verbose 실행:

```powershell
pwsh -File tools/ui_smoke/run.ps1 -Verbose
```

```bash
bash tools/ui_smoke/run.sh --verbose
bash tools/ui_smoke/run.sh -v
```

느린 환경에서는 `UI_SMOKE_TIMEOUT_SECONDS` 또는 wrapper의 timeout 인자로 제한 시간을 늘릴 수 있다. 실패한 임시 폴더를 유지하려면 `--keep-temp` 또는 `KEEP_UI_SMOKE_TEMP=1`을 사용한다.

기본 non-verbose 실행은 성공 시 통과/스킵/실패 개수와 소요 시간만 출력한다. 실패 시에는 실패 단계, 원인, exit code에 해당하는 runner 실패 메시지, 진단 폴더 경로만 출력한다. `-Verbose`, `--verbose`, `-v` 실행은 성공/실패 단계별 세부 정보와 앱 stdout/stderr 로그를 함께 출력한다.

자동 smoke는 `filedialog.askdirectory`를 임시 workspace 경로로 patch한 뒤 실제 사이드바 `등록` 버튼 command를 `invoke`한다. 사이드바 토글, 예약실행, 세션/프리셋 AI 설정, 세션 생성, 작업 등록, fake 큐 실행, 작업 프롬프트 보기, 프리셋 입력 UI, manual 후보 UI, 가져오기, About, Licenses, 설정 저장도 가능한 범위에서 실제 Tk 버튼 command 경로를 사용한다.

| 단계 | 검증 방식 | 기대 결과 | 실패 시 확인 artifact |
| --- | --- | --- | --- |
| 앱 실행 | `main.py --ui-smoke-report ... --data-dir ...` 프로세스 실행, report action `launch_app` 확인 | 앱 버전과 window geometry가 report에 기록됨 | `logs/app-stdout.log`, `logs/app-stderr.log`, `logs/ui-smoke-report.json` |
| workspace 등록 | `askdirectory` patch 후 사이드바 등록 버튼 `invoke` | 임시 workspace 탭이 열리고 runtime workspace가 생성됨 | `logs/ui-smoke-report.json`, `logs/window-diagnostics.json` |
| 사이드바 토글 | 상태바 사이드바 토글 버튼 2회 `invoke` | 사이드바가 접힌 뒤 다시 펼쳐짐 | `logs/ui-smoke-report.json`, `logs/window-diagnostics.json` |
| 예약실행 다이얼로그 | 예약실행 버튼 `invoke`, 저장 후 기존 예약 취소 버튼 경로 재실행 | 예약 시각이 설정된 뒤 취소됨 | `logs/ui-smoke-report.json`, `logs/window-diagnostics.json` |
| 새 일반 세션 생성 | workspace 헤더의 새 세션 버튼 `invoke` | 새 session tab과 프롬프트 편집기가 생성됨 | `logs/window-diagnostics.json` |
| 세션 AI 설정 | 세션 상단 `AI Settings` 버튼 `invoke`, 저장 버튼 경로 실행 | 모달이 열리고 실행 옵션 결과가 저장됨 | `logs/ui-smoke-report.json`, `logs/window-diagnostics.json` |
| 프롬프트 입력 | 실제 `Text` 위젯에 smoke prompt 삽입 후 다시 읽기 | 입력 문자열이 그대로 유지됨 | `logs/window-diagnostics.json` |
| 작업 등록 버튼 | 일반 세션 등록 버튼 `invoke` | runtime job이 등록되고 프롬프트 입력이 처리됨 | `logs/ui-smoke-report.json`, `storage/j3AITaskRunner.json` |
| 작업 목록/runtime 확인 | runtime job 목록과 workspace 작업 `Treeview` row 값 확인 | smoke prompt job과 auto-commit job이 runtime과 작업 목록에 같은 순서/세션/상태/프롬프트로 보임 | `logs/ui-smoke-report.json`, `logs/window-diagnostics.json` |
| auto-commit follow-up | runtime job prompt 목록 확인 | `AUTO_COMMIT_PROMPT` follow-up job이 같은 세션에 등록됨 | `logs/ui-smoke-report.json` |
| 진행 로그 영역 | 세션 body notebook 선택 탭과 로그 위젯 존재 확인 | 등록 후 진행 로그 탭이 선택되거나 로그 영역이 존재함 | `logs/window-diagnostics.json` |
| 프롬프트 보기 | 등록된 작업의 프롬프트 보기 command 실행 | 프롬프트 보기 모달이 열리고 smoke prompt를 표시한 뒤 닫힘 | `logs/ui-smoke-report.json` |
| fake 큐 실행 | workspace 큐 토글 버튼 `invoke`, fake Codex JSONL/마지막 응답 파일 생성 대기 | smoke prompt와 auto-commit job이 완료되고 진행 로그, 이력, 작업 목록 완료 상태가 렌더링됨 | `logs/ui-smoke-report.json`, `logs/app-stdout.log`, `logs/app-stderr.log` |
| 새 프리셋 세션 | workspace 헤더의 새 프리셋 버튼 `invoke`, 프리셋 옵션 로딩 대기 | 프리셋 탭, 입력 콤보박스, prefix 편집기, 후보 탭이 생성됨 | `logs/ui-smoke-report.json`, `logs/window-diagnostics.json` |
| manual 후보 UI | 프리셋 후보 탭에 smoke 후보를 렌더링하고 체크 상태를 반영 | 후보 체크 후 `Continue` 버튼이 활성화됨 | `logs/ui-smoke-report.json`, `logs/window-diagnostics.json` |
| 프리셋 AI 설정 | 프리셋 등록줄 `AI Settings` 버튼 `invoke`, 저장 버튼 경로 실행 | 모달이 열리고 실행 옵션 결과가 저장됨 | `logs/ui-smoke-report.json` |
| 가져오기 다이얼로그 | workspace 헤더의 가져오기 버튼 `invoke`, 다중 prompt 텍스트 입력 후 등록 | import 세션과 prompt/auto-commit job이 등록되고 첫 import 세션/작업이 UI에서 선택됨 | `logs/ui-smoke-report.json`, `storage/j3AITaskRunner.json` |
| About 다이얼로그 | About 버튼 `invoke`, smoke subclass가 일정 시간 뒤 닫기 | 다이얼로그가 열리고 닫힘 상태가 report에 기록됨 | `logs/ui-smoke-report.json` |
| Licenses 다이얼로그 | 설정 다이얼로그 안의 Licenses 버튼 `invoke` | 라이선스 고지 모달이 열리고 닫힘 | `logs/ui-smoke-report.json` |
| 설정 다이얼로그 저장 | 설정 버튼 `invoke`, 설정 다이얼로그 저장 버튼 `invoke` | 기본 설정 저장 결과가 runtime에 반영되고 fake Codex provider/executable 설정이 유지됨 | `logs/ui-smoke-report.json`, `storage/j3AITaskRunner.json` |
| persistence 확인 | 앱 종료 전후 `storage/j3AITaskRunner.json` 읽기 | workspace와 fake Codex executable 설정이 남아 있음 | `storage/j3AITaskRunner.json` |

## Manual Smoke

manual smoke는 릴리즈 전 UI 체감, DPI, 다이얼로그 배치처럼 자동 report만으로 보기 어려운 항목을 사람이 확인할 때 사용한다. 자동 smoke의 대체 증거가 아니며, 실제 AI CLI 대신 fake executable과 임시 `--data-dir`을 사용한다.

권장 순서:

1. 표준 wrapper로 자동 smoke를 먼저 통과시킨다.
2. 별도 임시 폴더에 fake executable과 `--data-dir`을 준비해 앱을 실행한다.
3. workspace 등록, 새 일반 세션, 프롬프트 입력, 등록, 진행 로그 탭 전환, About, 설정 저장을 화면에서 확인한다.
4. 종료 후 임시 `storage/j3AITaskRunner.json`에 workspace와 fake executable 설정이 남았는지 확인한다.

manual smoke 중에도 실제 provider/AI CLI 경로를 설정하지 않는다. 큐 실행까지 직접 확인하려면 fake executable은 `--version` 요청에 짧은 문자열을 출력하고, `codex exec --json ... -o <path> -` 형태의 실행 요청에는 유효한 JSONL과 마지막 응답 파일을 생성해야 한다.

## 격리 원칙

자동 smoke는 앱 프로세스 환경에 다음 임시 경로만 노출한다.

- `HOME`, `USERPROFILE`
- `APPDATA`, `LOCALAPPDATA`
- `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`
- `TMPDIR`, `TMP`, `TEMP`
- 앱 `--data-dir`

실제 사용자 profile, 실제 `data/`, 실제 provider executable은 사용하지 않는다.

## 실패 진단

실패 시 wrapper가 임시 폴더를 보존하고 `tools/ui_smoke/artifacts/latest`로 진단 파일을 복사한다. 기존 `latest`가 있으면 새 실패 진단으로 교체한다. 성공 시에는 `latest` 갱신을 보장하지 않으므로 성공 증거로 이 폴더를 사용하지 않는다. 우선 확인할 파일은 다음과 같다.

`tools/ui_smoke/artifacts/`는 UI smoke wrapper가 만드는 진단 생성물이며 `.gitignore`와 AI 작업 제외 기준에 포함된다.

- `logs/app-stdout.log`: 앱 프로세스 stdout
- `logs/app-stderr.log`: 앱 프로세스 stderr와 Tk callback traceback
- `logs/ui-smoke-report.json`: `last_action`, 앱 버전, 실행 action 목록, 사용자 메시지, traceback, job/dialog/persistence 관찰 결과
- `logs/window-diagnostics.json`: window geometry, workspace/session/widget 상태 snapshot
- `storage/j3AITaskRunner.json`: 저장된 workspace/settings persistence

screenshot은 남기지 않는다. Tk 화면 캡처는 OS/display 서버와 권한 차이가 커 추가 의존성 없이 안정적으로 보장하기 어렵기 때문에, 자동 smoke는 widget state snapshot과 window diagnostics JSON으로 대체한다.

## Headless 실행

Linux headless 환경에서 `DISPLAY`가 없고 `xvfb-run`이 있으면 `run.sh`가 자동으로 `xvfb-run -a`를 사용한다. CI의 실행 위치와 Python 버전 해석 기준은 [`../../docs/build-and-test.md`](../../docs/build-and-test.md)를 따른다.

## CI 실행 경로

GitHub Actions는 [`.github/workflows/test.yml`](../../.github/workflows/test.yml)에서 `bash tools/ui_smoke/run.sh` 표준 wrapper를 호출한다. 실패하면 `tools/ui_smoke/artifacts/latest`가 `ui-smoke-diagnostics` artifact로 업로드된다.
