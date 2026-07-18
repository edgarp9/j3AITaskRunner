# 플랫폼별 주의사항 문서

Windows/Linux 실행 차이, Tkinter/DPI, 외부 프로세스, provider CLI 변동 위험을 확인할 때 읽는다.

## Python과 Tkinter

- CI 기준 Python은 3.12다. 로컬 Python 버전이 다르면 실패를 CI와 같은 조건의 실패로 단정하지 말고 3.12 또는 원격 CI에서 재확인한다.
- 소스 실행과 기본 unittest는 표준 라이브러리와 Tkinter를 기준으로 한다.
- Linux 환경에서는 Python Tk 지원 패키지가 별도로 필요할 수 있다.
- Linux headless UI smoke는 `xvfb`와 `xauth` 설치 후 wrapper가 `xvfb-run`을 사용한다.

## Windows DPI

- Tk 루트 생성 전에 Windows DPI awareness를 한 번 시도한다.
- 관리 도구형 UI 안정성을 위해 `SYSTEM_AWARE`를 먼저 시도하고 실패 시 per-monitor context, shcore system/per-monitor, legacy `SetProcessDPIAware()` 순서로 fallback한다.
- Tk 루트 생성 직후 현재 DPI를 읽어 `tk scaling`을 적용한다.
- 기본 Tk UI 폰트는 Windows에서 `Malgun Gothic` 계열, 고정폭 로그 영역은 `Consolas`를 유지한다.
- `UiScale`은 frame padding, panel width, Treeview rowheight, classic widget border/highlight 같은 픽셀 기반 값에만 적용한다. 문자 수 기반 `width`/`height`는 스케일하지 않는다.
- DPI callback에서는 필요한 widget option과 style만 다시 적용하고 창 `geometry()`를 되먹임하지 않는다.

## 창 크기와 모니터

- 메인 윈도우 Tk client geometry는 실행 시 1100 x 800, 최소 800 x 600이다.
- 왼쪽 사이드바 초기 폭은 180 px, 접힌 폭은 0 px이다.
- 워크스페이스 탭 내부 오른쪽 작업 목록 초기 폭은 180 px이고 세션 영역은 남은 폭을 사용한다.
- 팝업과 설정 대화상자는 메인 창 기준 중앙에 배치한다. 음수 좌표 모니터에서도 좌표를 0으로 보정하지 않는다.

## 외부 프로세스와 timeout

- Windows 백그라운드 프로세스 시작 시 새 콘솔 창이 뜨지 않도록 콘솔 창 생성 억제 옵션을 적용한다.
- 세션 종료 훅은 `subprocess.Popen` fire-and-forget으로 실행하며 `shell=True`를 사용하지 않는다. `cwd`는 세션 워크스페이스 path이고 stdin/stdout/stderr는 수집하지 않는다.
- Windows 세션 종료 훅도 provider 실행과 같은 hidden console creationflags helper를 사용한다.
- timeout 또는 사용자 취소로 외부 실행기를 종료할 때는 가능한 플랫폼 기능으로 부모 프로세스뿐 아니라 프로세스 트리 종료를 시도한다.
- 종료 요청 뒤에도 reader가 끝나지 않거나 프로세스가 종료되지 않으면 무기한 대기하지 않고 내부 경고를 남긴 뒤 결과를 확정해 실행 슬롯을 비운다.
- `execution_timeout_minutes=0`은 전체 실행 제한 비활성화, `inactivity_timeout_minutes=0`은 무활동 제한 비활성화다.
- `termination_grace_seconds=0`은 정상 종료 유예 없이 강제 종료를 뜻한다.

## Provider CLI 변동 위험

- Codex CLI 계약은 alpha 계열 변경 가능성이 있다. `codex exec --json`, `codex exec resume --json`, `--skip-git-repo-check`, `-o <last_message_file>` 동작이 바뀌면 adapter와 tests를 함께 갱신한다.
- Claude Code, Kilo Code, OpenCode, Pi Coding Agent는 현재 프롬프트를 argv 위치 인자로 전달하므로 Windows command line 길이 제한 위험이 있다.
- OpenCode/Kilo Code stdout schema와 Pi JSON Event Stream schema는 로컬 설치 버전별로 바뀔 수 있다.
- 실제 CLI 계약은 `J3AITASKRUNNER_RUN_REAL_AGENT_SMOKE=1` opt-in smoke와 로컬 `--help` 또는 JSON mode 결과로만 재검증한다.
- provider별 모델 후보는 CLI 버전, 계정 권한, 서비스 정책에 따라 달라질 수 있다. 후보 목록 변경 시 실제 CLI 또는 공식 문서로 재확인한다.

## 제외 경로

저장소 전체 검색/검증 시 루트 [`../AGENTS.md`](../AGENTS.md)의 제외 경로와 [`.gitignore`](../.gitignore)를 따른다. 특히 `.build-venv/`, `.j3aitaskrunner/`, 루트 `j3AITaskRunner.json`, `tools/ui_smoke/artifacts/`, `dist/`, `build/`, `lib/`, `log/`, `data/`, `tmp_validation/`, cache 디렉터리와 바이너리/작업 파일은 일반 탐색/수정 대상에서 제외한다.
