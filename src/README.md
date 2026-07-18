# j3AITaskRunner

여러 워크스페이스를 동시에 열고, 워크스페이스별 세션 탭에 AI 실행 작업을 등록해 큐로 실행하는 Python/Tkinter 데스크톱 앱이다. 외부 AI 실행기는 Codex CLI, Claude Code, Kilo Code, OpenCode, Pi Coding Agent 같은 provider CLI를 별도 프로세스로 실행한다.

이 문서는 저장소를 처음 볼 때 읽는 진입 문서다. AI 에이전트의 작업 규칙은 [AGENTS.md](AGENTS.md), 작업 유형별 상세 문서 선택은 [docs/README.md](docs/README.md)를 따른다.

## Python 기준

- CI 기준 Python 버전은 3.12다.
- 소스 실행과 기본 테스트는 표준 라이브러리와 Tkinter를 기준으로 한다.
- Linux 환경에서는 Python Tk 지원 패키지가 별도로 필요할 수 있다.
- 선택적 개발/빌드 의존성은 [requirements-dev.txt](requirements-dev.txt)에 둔다.

## 실행 방법

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

시작 시 열 워크스페이스를 넘길 수 있다.

```bash
python main.py <workspace_path> [<workspace_path> ...]
```

격리된 설정/데이터 경로로 실행하려면 `--data-dir`를 사용한다.

```bash
python main.py --data-dir <path>
```

릴리즈 빌드는 다음 명령으로 실행한다.

```bash
python build_release.py
```

`build_release.py`는 플랫폼별 `.build-venv/<platform>/`를 준비하고 PyInstaller 6 이상과 `tkinterdnd2`를 설치한 뒤 PyInstaller `onedir` 산출물을 만든다.

## 테스트 방법

CI 기준 Python 버전은 3.12다. 로컬 Python 버전이 다르면 결과 해석이 달라질 수 있으므로 세부 기준은 [docs/build-and-test.md](docs/build-and-test.md)를 따른다.

자주 쓰는 로컬 검증:

```bash
python -m compileall main.py app domain infra ui tests tools/ui_smoke
python -m unittest tests.test_static_quality
python -m unittest
```

환경에 설치되어 있을 때만 사용하는 선택 검증:

```bash
python -m pytest
ruff check .
```

GitHub Actions는 Python 3.12에서 `compileall`, 전체 `unittest`, Linux UI smoke를 실행한다.

## UI smoke

UI smoke 실행 여부와 `tests.test_ui_smoke_contract`만 실행해도 되는 기준은 [docs/build-and-test.md](docs/build-and-test.md)를 따른다. wrapper 사용법과 실패 artifact 해석은 [tools/ui_smoke/README.md](tools/ui_smoke/README.md)를 따른다.

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
```

## 개발 의존성

일반 소스 실행과 `unittest`에는 별도 pip 설치가 필수는 아니다. 로컬에서 drag-and-drop 지원이나 릴리즈 빌드 의존성을 미리 맞추려면 다음을 사용할 수 있다.

```bash
python -m pip install -r requirements-dev.txt
```

릴리즈 빌드 자체는 `build_release.py`가 관리하는 별도 `.build-venv/<platform>/`에서 필요한 빌드 의존성을 다시 확인한다.

## 문서 라우팅

- [AGENTS.md](AGENTS.md): AI 에이전트용 진입 규칙, 명령, 구조, 금지 영역.
- [docs/README.md](docs/README.md): 작업 유형별 문서 라우터.
- [docs/features.md](docs/features.md): 기능 설명, 도메인 용어, 큐/세션/프리셋/provider 계약.
- [docs/architecture.md](docs/architecture.md): 계층 책임, 코드 구조, 수정 시작점.
- [docs/build-and-test.md](docs/build-and-test.md): 실행, 테스트, UI smoke.
- [docs/ui.md](docs/ui.md): Tkinter UI 기준.
- [docs/platform-notes.md](docs/platform-notes.md): Windows/Linux, DPI, 외부 프로세스, provider CLI 주의사항.
- [docs/release.md](docs/release.md): PyInstaller 릴리즈 빌드와 배포 전 검증.
- [docs/license.md](docs/license.md): 라이선스와 고지 준수.
- [docs/decisions.md](docs/decisions.md): 현재 유효한 기술 결정과 운영 규칙.

## 주요 디렉터리

- [main.py](main.py): 앱 실행 진입점.
- [app/](app/): 런타임, controller, scheduler, use case.
- [domain/](domain/): I/O 없이 검증 가능한 모델과 정책.
- [infra/](infra/): 저장소, provider 실행, 파일시스템과 OS 연동.
- [ui/](ui/): Tkinter 화면, 다이얼로그, UI 표시 보조.
- [tests/](tests/): unittest 기반 계약/회귀 테스트.
- [tools/ui_smoke/](tools/ui_smoke/): 실제 Tkinter 앱 프로세스를 띄우는 UI smoke wrapper.
- [prompt/](prompt/): j3aiPromptLoop 호환 프리셋 프롬프트 자산.
- [assets/](assets/): 앱 아이콘과 UI 리소스.
- [docs/](docs/): 영역별 현재 계약 문서.
- [.github/workflows/test.yml](.github/workflows/test.yml): CI 검증 흐름.
