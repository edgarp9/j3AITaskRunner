# 아키텍처 문서

계층 책임, 현재 코드 구조, 수정 시작점을 확인할 때 읽는다. 현재도 유효한 설계 결정 요약은 [`decisions.md`](decisions.md)를 함께 확인한다.

## 현재 구조

```text
main.py
ui/      MainWindow, view/action mixin, dialogs, i18n, DPI/windowing/theme
app/     controller, runtime, workspace/session manager, scheduler, use case, execution worker
infra/   process runner, provider adapter/JSONL parser, prompt store, repository, OS 연동
domain/  models, policies, preset처럼 I/O 없이 검증 가능한 규칙
tests/   계층별 unit/integration/smoke 계약 테스트
docs/    영역별 현재 계약
```

의존 방향은 `ui -> app -> infra/domain`을 기본으로 한다. `domain`은 `app`, `infra`, `ui`를 import하지 않고, `app`과 `infra`는 `ui`를 import하지 않는다. 이 규칙과 production Python 파일 1500줄 제한은 `tests/test_static_quality.py`가 확인한다.

## 계층 책임

| 계층 | 책임 |
| --- | --- |
| `ui` | Tkinter 창/프레임/다이얼로그/이벤트 바인딩, 워크스페이스/세션/작업 목록 표시, 진행 로그와 이력 렌더링, 사용자 입력 전달. |
| `app` | 워크스페이스/세션/작업 런타임 상태, 입력 검증, 설정 조회, 실행 옵션 스냅샷 적용, scheduler 조정, 프리셋 후속 흐름, UI 이벤트 연결. |
| `infra` | 로컬 파일 저장, 외부 프로세스 실행, provider 명령/환경/stdout/stderr/마지막 응답 처리, prompt 자산 읽기, 파일시스템/플랫폼 연동. |
| `domain` | 워크스페이스/세션/작업 식별, 이름/정렬/우선순위/상태 전이처럼 I/O 없이 검증 가능한 정책. |

## Facade와 분리 원칙

- `ui/main_window.py`는 Tk 루트 초기화, mixin 합성, startup workspace 진입점의 facade다.
- `app/runtime.py`는 UI-facing event 타입, 설정/프리셋 상수, background queue/thread, `AppRuntime` 합성의 중심이다.
- `app/scheduler.py`는 공개 import 경로를 유지하되 타입, 정렬, dispatch, lifecycle, queue state, query 책임을 전용 모듈로 나눈다.
- `infra/process_runner.py`는 provider adapter factory, 공통 alias, 프로세스 종료, timeout/file helper, compatibility export를 가진 facade다.
- `_main_window_global`, `_runtime_global`, `_process_runner_global`은 facade 이름을 늦게 찾는 순환 import 회피 패턴이다. 새 이름을 늦게 조회하게 만들 때는 facade에 실제로 존재하는지 확인한다.

## MainWindow 분리 기준

- `MainWindow` 본문은 화면 생성, 이벤트 연결, `AppRuntime` 이벤트 poll, Tk 위젯 갱신 조정만 담당한다.
- `WorkspaceWidgets`, `SessionWidgets`, `ExecutionOptionControls`, `RuntimeUiUpdateBatch`는 UI 상태 컨테이너다.
- 세션 이력 텍스트 포맷, 렌더 캐시 비교, 변경 지점 계산은 Tk 위젯을 직접 조작하지 않는 렌더링 보조 책임이다.
- 워크스페이스 작업 목록의 컬럼 정의, 폭 계산, Treeview 행 값 생성, stale 행 제거, 선택 유지, 요약 문구 갱신은 `ui.workspace_tasks` 책임이다.
- 파일 I/O, 외부 프로세스 실행, 저장소 접근은 `MainWindow`에 두지 않는다.

## 수정 시작점

| 영역 | 시작 파일 | 관련 테스트 |
| --- | --- | --- |
| UI shell | `ui/main_window.py`, `ui/main_window_state.py`, `ui/main_window_shared.py`, `ui/main_window_layout.py`, `ui/main_window_events.py`, `ui/main_window_lifecycle.py` | `tests/test_main_window*.py`, `tests/test_static_quality.py` |
| 워크스페이스/세션 탭 | `ui/main_window_workspace_views.py`, `ui/main_window_session_views.py`, `ui/main_window_tab_actions.py`, `ui/main_window_workspace_actions.py` | `tests/test_main_window_workspace.py`, `tests/test_main_window_sessions.py`, `tests/test_main_window_session_selection.py` |
| 세션 출력/이력/작업 목록 | `ui/main_window_session_rendering.py`, `ui/workspace_tasks.py`, `ui/main_window_job_actions.py` | `tests/test_main_window_session_rendering.py`, `tests/test_main_window_workspace.py`, `tests/test_messages.py` |
| 실행 옵션 UI | `ui/main_window_execution_widgets.py`, `ui/main_window_execution_controls.py`, `ui/agent_settings_dialog.py`, `ui/settings_dialog_ai.py` | `tests/test_main_window_execution_options.py`, `tests/test_agent_cli_options.py`, `tests/test_agent_cli_version.py` |
| 프리셋 UI | `ui/main_window_preset.py`, `ui/main_window_preset_options.py` | `tests/test_main_window_presets.py`, `tests/test_main_window_sessions.py` |
| Runtime | `app/runtime.py`, `app/runtime_workspace.py`, `app/runtime_queue.py`, `app/runtime_queue_control.py`, `app/runtime_workers.py` | `tests/test_app_runtime*.py`, `tests/test_execution_worker.py`, `tests/test_timeout_smoke.py` |
| 프리셋 runtime | `app/runtime_preset_api.py`, `app/runtime_preset_flow.py`, `app/runtime_preset_work_generation.py`, `app/use_cases.py` | `tests/test_preset_flow*.py`, `tests/test_bulk_import.py` |
| Controller/Scheduler | `app/controller.py`, `app/scheduler*.py` | `tests/test_controller*.py`, `tests/test_scheduler*.py`, `tests/test_app_runtime*.py` |
| Provider/process runner | `infra/agent_contract.py`, `infra/process_runner*.py`, `infra/*_adapter.py`, `infra/*_jsonl.py` | `tests/test_process_runner*.py`, `tests/test_agent_cli*.py`, `tests/test_timeout_smoke.py` |
| Persistence/use case | `infra/repository.py`, `app/use_cases.py`, `app/workspace_manager.py`, `app/session_manager.py` | `tests/test_persistence*.py`, `tests/test_app_runtime*.py`, `tests/test_bulk_import.py` |
| Prompt 자산 | `prompt/`, `infra/prompt_store.py`, 프리셋 runtime 파일 | `tests/test_persistence.py`, `tests/test_preset_flow_parsing.py`, `tests/test_preset_flow*.py` |
