# AGENTS.md

`j3AITaskRunner`는 여러 워크스페이스와 세션 탭의 AI Agent CLI 작업을 큐로 실행하는 Python/Tkinter 데스크톱 앱이다.

항상 한국어로 답변한다. 코드, 명령, 커밋 메시지, 주석은 필요할 때 영어를 섞을 수 있다.

## 먼저 확인할 것

- 요청 범위와 변경 대상 파일을 먼저 확인한다.
- 변경 전 [`docs/README.md`](docs/README.md)에서 작업 유형에 맞는 문서만 고른다. 모든 문서를 무조건 읽지 않는다.
- 기존 구조, 파일 배치, 네이밍, 호출 흐름, helper를 먼저 확인하고 재사용한다.
- 코드 수정 뒤에는 가능한 최소 1개 이상의 검증을 수행한다.
- 문서만 수정한 경우 코드 테스트는 생략할 수 있지만 문서 목록, 제목 구조, 상대 링크 대상 파일 존재 여부를 확인한다.

## 주요 구조

- `main.py`: 앱 실행 진입점.
- `app/`: runtime, controller, scheduler, use case, execution worker.
- `domain/`: I/O 없이 검증 가능한 모델과 정책.
- `infra/`: 저장소, provider 실행, prompt store, 파일시스템/OS 연동.
- `ui/`: Tkinter 화면, 다이얼로그, 렌더링, DPI/windowing/theme.
- `tests/`: unittest 기반 계약/회귀 테스트.
- `tools/ui_smoke/`: 실제 Tkinter 앱 프로세스를 띄우는 UI smoke wrapper.
- `prompt/`: j3aiPromptLoop 호환 프리셋 프롬프트 자산.
- `assets/`, `LICENSES/`, `THIRD_PARTY_NOTICES.txt`: 앱 리소스와 고지.
- `docs/`: 기능, 설계, UI, 빌드/테스트, 릴리즈, 라이선스, 플랫폼 주의사항, 결정사항.

## 실행, 테스트, 빌드

```powershell
py main.py
python main.py
python main.py <workspace_path> [<workspace_path> ...]
python main.py --data-dir <path>
```

```bash
python3 main.py
./main.py
python -m compileall main.py app domain infra ui tests tools/ui_smoke
python -m unittest tests.test_static_quality
python -m unittest
python build_release.py
```

UI smoke:

```powershell
pwsh -File tools/ui_smoke/run.ps1
```

```bash
bash tools/ui_smoke/run.sh
```

선택 검증인 `python -m pytest`, `ruff check .`는 해당 도구가 설치된 환경에서만 실행한다. 세부 기준은 [`docs/build-and-test.md`](docs/build-and-test.md)와 [`docs/release.md`](docs/release.md)를 따른다.

## 코딩 규칙

- Tkinter 메인 루프를 블로킹하지 않는다.
- 백그라운드 스레드에서 Tkinter 위젯을 직접 조작하지 않는다.
- UI 이벤트 핸들러에 파일 I/O, 네트워크, 긴 연산을 직접 섞지 않는다.
- 팝업창과 대화상자는 모달로 띄우고 메인 윈도우 기준 중앙에 배치한다.
- 플랫폼 전용 처리는 가능한 한 `infra` 또는 전용 helper에 둔다.
- 계층 방향은 `ui -> app -> infra/domain`을 기본으로 유지한다.
- 불필요한 인터페이스, 팩토리, 전략 패턴, 제네릭 레이어, 래퍼, 설정 기반 구조를 도입하지 않는다.
- `except: pass`로 예외를 숨기지 않는다. 내부 원인과 사용자 메시지를 분리한다.
- 새 의존성은 신중히 추가하고 표준 라이브러리와 기존 의존성을 우선한다.

## 변경 시 주의사항

- 작업, 큐, provider 실행 계약은 [`docs/features.md`](docs/features.md)를 먼저 확인한다.
- 계층 책임과 수정 시작점은 [`docs/architecture.md`](docs/architecture.md)를 따른다.
- UI/Tkinter 변경은 [`docs/ui.md`](docs/ui.md)와 [`docs/platform-notes.md`](docs/platform-notes.md)를 함께 확인한다.
- 현재 유효한 기술 결정은 [`docs/decisions.md`](docs/decisions.md)에 유지한다.
- 릴리즈 고지 파일(`LICENSE`, `LICENSES/*`, `THIRD_PARTY_NOTICES.txt`, `about.txt`)은 [`docs/license.md`](docs/license.md)와 [`docs/release.md`](docs/release.md)를 확인하지 않고 수정하지 않는다.
- `prompt/<Language>/*.md`는 프리셋 자산 변경 요청이 있을 때만 수정한다.

## 자주 사용하는 작업 절차

1. 기능/도메인 변경: [`docs/features.md`](docs/features.md)를 읽고 관련 `app/`, `domain/`, `infra`, `ui` 흐름을 확인한다.
2. Provider/CLI 변경: [`docs/features.md`](docs/features.md), [`docs/platform-notes.md`](docs/platform-notes.md)를 읽고 `infra/*_adapter.py`, `infra/*_jsonl.py`, `infra/process_runner*.py`를 확인한다.
3. UI 변경: [`docs/ui.md`](docs/ui.md), [`docs/platform-notes.md`](docs/platform-notes.md)를 읽고 `ui/main_window*.py`, `ui/*dialog*.py`, 관련 UI 테스트를 확인한다.
4. 구조 변경: [`docs/architecture.md`](docs/architecture.md), [`docs/decisions.md`](docs/decisions.md)를 읽고 계층 방향과 facade 책임을 확인한다.
5. 테스트 변경: [`docs/build-and-test.md`](docs/build-and-test.md)를 읽고 필요한 검증 범위를 고른다.
6. 릴리즈/라이선스 변경: [`docs/release.md`](docs/release.md), [`docs/license.md`](docs/license.md)를 읽고 배포물 포함 파일과 고지 노출 기준을 확인한다.

## 제외 대상

다음 경로와 패턴은 사용자가 명시하지 않는 한 읽기, 검색, 후보 탐색, evidence 인용, 수정 후보 산정, 생성, 수정, 삭제, 이동, 이름 변경, 포맷팅 대상에서 제외한다.

`dist/`, `.git/`, `.my/`, `build/`, `lib/`, `log/`, `__pycache__/`, `*.pyd`, `*.pyc`, `*.pyo`, `*$py.class`, `.venv/`, `.build-venv/`, `venv/`, `env/`, `ENV/`, `site-packages/`, `.ruff_cache/`, `.pytest_cache/`, `.mypy_cache/`, `.pyre/`, `.hypothesis/`, `.tox/`, `.nox/`, `.eggs/`, `*.egg-info/`, `pip-wheel-metadata/`, `.coverage`, `.coverage.*`, `coverage.xml`, `htmlcov/`, `*.log`, `*.tmp`, `*.bak`, `j3aiPromptLoop.json`, `j3AITaskRunner.json`, `.j3aitaskrunner/`, `.vscode/`, `.idea/`, `.DS_Store`, `Thumbs.db`, `Desktop.ini`, `*~`, `*.swp`, `*.swo`, `.nfs*`, `.fuse_hidden*`, `.directory`, `.Trash-*`, `.xsession-errors*`, `data/`, `tmp_validation/`, `tools/ui_smoke/artifacts/`

- `.my` 폴더는 접근하거나 분석하지 않는다.
- 제외 대상은 루트 [`.gitignore`](.gitignore)보다 우선하는 작업 규칙이다.
