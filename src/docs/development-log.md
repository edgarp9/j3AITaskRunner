# 개발 기록

## 2026-06-20 v0.2.0 버전 갱신

### 변경 요약

- 앱 단일 버전 기준인 `app/version.py`의 `APP_VERSION`을 `0.2.0`으로 갱신했다.
- 메인 창 제목, `--version` 출력, 설정 대화상자, Windows 릴리즈 버전 리소스는 기존 단일 상수 참조를 통해 새 버전을 사용한다.

### 최종 검증

- `python -m compileall -q -x '(^|[\\/])(\\.git|\\.my|dist|build|log|data|__pycache__|\\.venv|venv|env|ENV|site-packages|\\.ruff_cache|\\.pytest_cache|\\.mypy_cache|\\.pyre|\\.hypothesis|\\.tox|\\.nox|\\.eggs|htmlcov)([\\/]|$)|\\.py[co]$' .`
  결과: 통과.
- `python -m unittest tests.test_main tests.test_build_release`
  결과: 16개 실행, 1개 skip, 통과.

### 남은 리스크

- 실제 PyInstaller 릴리즈 빌드는 수행하지 않았다. 빌드 리소스 문자열 생성은 `tests.test_build_release`에서 검증했다.

## 2026-05-27 실행 계약 문서 정합성 갱신

### 변경 요약

- `docs/domain.md`의 오래된 "아직 Python/Tkinter 앱 소스가 없다" 설명을 현재 구현 구조 기준으로 수정했다.
- Provider 실행 계약, timeout/cancel 정책, 설정 migration, 파일 로그 저장 분리, known risk를 실제 구현 기준으로 재정리했다.
- 재현 조건과 회귀 테스트명을 `docs/domain.md`의 "재현/회귀 테스트 색인"에 남겼다.
- `tests/test_process_runner.py`의 timeout 로그 캡처 테스트에서 timeout 유발 시각 조작이 `assertLogs` 밖에서 먼저 실행되던 경쟁 조건을 수정했다.

### 재현 조건

- 실패 재현 명령:
  `python -m unittest tests.test_process_runner.ClaudeCodeProcessRunnerTests.test_claude_timeout_marks_result_failed`
- 원인:
  `runner.launch()` 직후 timeout monitor 스레드가 먼저 경고 로그를 남기면, 테스트의 `assertLogs("infra.process_runner")`가 `handle.wait()` 구간에서 같은 경고를 다시 잡지 못했다.
- 수정:
  timeout을 유발하는 `handle._started_monotonic` 또는 `handle._last_activity_monotonic` 변경을 `assertLogs` 범위 안으로 옮겼다. 같은 패턴의 Codex/Kilo timeout 테스트도 함께 정리했다.

### 최종 검증

- `python -m compileall .` 계열 검증:
  제외 경로를 읽지 않도록 `-x` 제외 정규식을 적용하고, `PYTHONDONTWRITEBYTECODE=1`로 pycache 생성을 막아 실행했다. 결과: 통과.
- 관련 테스트:
  `python -m unittest tests.test_process_runner tests.test_controller tests.test_persistence tests.test_execution_worker tests.test_timeout_smoke tests.test_domain_policies tests.test_app_runtime`
  결과: 215개 실행, 2개 skip, 통과.

### 남은 리스크

- 실제 Codex/Claude/Kilo/OpenCode CLI는 이 환경의 PATH에서 확인되지 않았다. 기본 검증은 fake executable/Popen 계약 테스트 기준이며, 실제 CLI 계약은 `J3AITASKRUNNER_RUN_REAL_AGENT_SMOKE=1` opt-in으로 별도 확인해야 한다.
- OpenCode/Kilo stdout JSON schema와 Codex CLI alpha 계약은 외부 CLI 버전에 따라 변할 수 있다.
