# j3AITaskRunner 도메인 문서

## 1. 문서 목적

- 이 문서는 `j3AITaskRunner`를 Python/Tkinter 기반 데스크톱 앱으로 구현할 때 기준이 되는 도메인 요구사항을 정의한다.
- 구현 전에 용어, 상태, 작업 흐름, 책임 경계를 먼저 고정해 UI 코드와 실행 로직이 섞이지 않도록 한다.
- 현재 저장소에는 Python/Tkinter 구현이 있으며, 본 문서는 구현 기준 계약과 앞으로 지켜야 할 요구사항을 함께 기록한다.

## 2. 프로젝트 개요

`j3AITaskRunner`는 여러 워크스페이스를 동시에 열고, 각 워크스페이스 안에서 여러 세션 탭을 운영하며, 워크스페이스별 작업 큐를 서로 독립적으로 실행하는 데스크톱 앱이다.

이 앱은 다음 문제를 해결한다.

- 여러 워크스페이스를 동시에 열어 놓고 전환하고 싶다.
- 워크스페이스마다 여러 세션 탭을 열어 서로 다른 대화 흐름을 나누고 싶다.
- 여러 작업 요청을 세션 탭별로 등록하고 순서대로 실행하고 싶다.
- j3aiPromptLoop의 프리셋 실행 흐름을 가져와 분석 프롬프트에서 여러 후보 작업 세션을 자동으로 만들고 싶다.
- 여러 ` ```text ` 코드 블록으로 작성된 지시문을 한 번에 가져와 블록별 새 일반 세션과 작업으로 등록하고 싶다.
- 실행 중 로그와 완료 결과를 분리해서 보고 싶다.
- 세션 이력에는 작업 시작 시 `Prompt:`를 즉시 남기고, 완료 응답이 확정되면 같은 턴에 `Response:`를 채우고 싶다.
- 진행 로그는 설정과 무관하게 UI에 항상 표시하고, 파일 로그 저장 옵션은 실행 로그를 파일로 저장할지 여부만 제어한다.
- 앱을 다시 열어도 기본 설정과 워크스페이스 목록은 유지하고 싶다.

## 3. 확인된 사실

- 저장소에는 `main.py`, `ui/`, `app/`, `domain/`, `infra/`, `tests/` 기반의 Python/Tkinter 앱 소스와 자동 테스트가 있다.
- 프로젝트명은 `j3AITaskRunner`이다.
- 런타임의 기본 구조는 `워크스페이스 탭 -> 세션 탭 -> 작업(Job)` 계층이다.
- 워크스페이스 탭과 워크스페이스 폴더 path는 1대1 관계다.
- 하나의 워크스페이스 탭은 여러 세션 탭을 가질 수 있다.
- 세션 탭 종류는 일반 세션, 프리셋 세션, 프리셋 후보 세션으로 나눌 수 있다.
- 프리셋 실행 기능은 j3aiPromptLoop의 `prompt/<Language>/<instruction>.md` 분석 프롬프트와 대응하는 `<instruction>_work.md` 작업 프롬프트 템플릿을 사용한다.
- 외부 실행기는 Codex CLI, Claude Code, Kilo Code, OpenCode, Pi Coding Agent 같은 Agent CLI를 별도 프로세스로 실행하는 `AI 실행기(provider)`로 취급하며, 앱은 해당 프로세스의 실행 요청, 로그 수집, 상태 저장을 담당한다.
- 지원 대상 provider 식별자는 `codex`, `claude_code`, `kilo_code`, `opencode`, `pi`다. `codex`는 현재 구현 기준 기본 provider로 유지한다.
- 외부 실행기는 절대/상대 파일 경로, 선택된 AI 실행기 실행 파일이 들어 있는 디렉터리, 또는 `codex`, `claude`, `kilo`, `opencode`, `pi`처럼 PATH에서 찾을 수 있는 명령 이름으로 설정할 수 있어야 한다. 디렉터리를 설정하면 provider별 후보명으로 실행 파일을 찾는다.
- 소스 실행은 Windows와 Linux에서 같은 `main.py` 진입점을 사용하며, Linux에서는 `python3 main.py` 또는 실행 권한이 있는 `./main.py`, Windows에서는 `py main.py` 또는 `python main.py`를 사용한다.
- AI 실행기 provider별 실행 옵션(모델, 추론 레벨 또는 variant 등)은 설정의 기본값으로 새 워크스페이스/세션에 초기 적용하고, 세션 상단에서 바꾼 선택값은 작업 등록 시점에 작업에 고정한다.
- 실행 요청에서 timeout, 파일 로그, 실행 파일 경로 같은 실행 운영 설정 스냅샷은 `operational_settings` 필드로 전달하고, 모델/추론 값은 계속 `execution_options`에서 읽는다.
- 앱 버전은 `app/version.py`의 `APP_VERSION`을 단일 기준으로 관리하며, 메인 창 제목, 설정 대화상자, About 창 상단 버전 라벨에 표시한다.
- 실제 CLI 설치 여부와 계정별 모델 접근권은 로컬 환경에 따라 달라지므로 기본 자동 테스트는 fake executable/Popen 기반 계약 테스트로 수행한다. 실제 CLI 스모크는 명시적 환경 변수 opt-in으로만 실행한다.
- 현재 구현된 자동 실행 경로는 기본 대화형 `codex`가 아니라 Codex provider의 비대화형 `codex exec --json` 및 `codex exec resume --json`을 사용해야 한다.
- Codex provider의 세션 ID는 표준 출력 JSONL의 `thread.started.thread_id` 또는 `codex.thread.started.thread_id`에서 얻을 수 있다.
- 지속 세션을 유지해야 하는 자동 실행 경로에서는 provider별 ephemeral 또는 임시 세션 옵션을 사용하지 않는다.
- 표준 출력은 provider별 계약에 따라 구조화 이벤트 또는 텍스트 로그로 파싱하고, 표준 오류는 경고와 진단 로그가 섞일 수 있어 사용자 응답 파싱 기준으로 사용하지 않는다.
- 외부 실행기 프로세스의 현재 작업 디렉터리(`cwd`)는 항상 워크스페이스 path로 지정한다. provider가 별도 작업 디렉터리 옵션을 제공하면 프로세스 `cwd`와 같은 워크스페이스 path를 중복 전달할 수 있다.
- Codex provider의 `codex exec resume`도 Git trusted directory 검사에 걸릴 수 있으므로 자동 실행 경로에는 `--skip-git-repo-check`를 포함해야 한다.
- Codex provider의 `codex exec resume`는 `-C/--cd` 옵션을 제공하지 않고 저장된 세션의 작업 루트를 재사용하므로, 재개 실행의 작업 루트 기준은 세션 탭에 연결된 세션 ID다.
- 세션 ID와 워크스페이스 path가 불일치하는 상태는 앱 내부 상태 관리 버그로 취급한다.
- 세션 ID는 세션 탭과 1:1로 매핑되며, 선택된 AI 실행기 실행으로 확정되기 전까지는 비어 있을 수 있다.
- 작업 큐, 세션 탭, 프리셋 흐름, 진행 로그, 파일 로그 저장, timeout 감시, 프로세스 트리 종료 요구사항은 provider가 바뀌어도 유지되어야 하는 앱 공통 계약이다.
- provider별 실행 계약은 실행 명령, 작업 디렉터리 전달, 프롬프트 전달 방식, 세션 ID 획득, 세션 재개, stdout/stderr 파싱, 마지막 응답 추출, 모델/추론 옵션, 권한/자동 승인 정책, 버전 조회를 분리해 정의해야 한다.
- 설정 대화상자의 실행기 버전 조회도 provider별 `AgentCliAdapter.build_version_command` 계약을 사용해 실행 명령을 구성한다.
- 앱 공통 실행 흐름은 `AgentRunRequest`, `AgentRunResult`, `AgentRunStatus`, `AgentStreamEvent`, `AgentParseSummary`, `AgentCliAdapter` 계약에 의존하고, provider별 실행 명령/환경/stdout 파싱/세션 ID/완료 판정/마지막 응답 추출은 adapter가 담당한다.
- Codex provider는 현재 구현된 기본 adapter이며, 기존 `codex exec --json` 및 `codex exec resume --json` 명령 형태와 `turn.completed`/마지막 응답 파일 기반 완료 판정을 유지한다.
- Claude Code provider는 `claude -p <prompt> --output-format stream-json --verbose --include-partial-messages [--resume <session_id>] [--model <model>]` 명령 형태를 사용한다. 현재 adapter는 문서화된 prompt argument 형태를 사용하고, 긴 프롬프트는 Windows command line 길이 제한 위험을 launch metadata와 로그에 남긴다.
- Claude Code provider의 세션 ID는 `system/init` 또는 `result` stream-json 이벤트의 `session_id`에서 얻으며, 실제 `~/.claude` 세션 저장소를 훑어 추론하지 않는다.
- Claude Code provider의 성공 판정은 `result` 이벤트의 `subtype=success`와 최종 응답 텍스트를 확인한 경우로 제한한다. 알 수 없는 stream-json 이벤트는 진행 로그로 보존하지만 명확한 완료 증거로 취급하지 않는다.
- Claude Code provider는 기본 명령에 `--dangerously-skip-permissions` 또는 `bypassPermissions` 계열을 포함하지 않는다.
- OpenCode provider는 `opencode run --format json --dir <workspace> [--session <id>] [--model <value>] [--variant <value>] <prompt>` 명령 형태를 사용하고, Kilo Code provider는 같은 OpenCode 계열 계약에서 실행 명령 이름만 `kilo`로 분리한다.
- OpenCode/Kilo Code provider의 현재 구현 근거는 문서화된 `run` 계약과 fake executable/Popen 계약 테스트다. 로컬 설치 버전의 help가 다르면 로컬 help를 우선해 adapter 계약을 갱신해야 한다.
- Pi Coding Agent provider는 `pi --mode json [--session <id>] [--model <value>] [--thinking <level>] <prompt>` 명령 형태를 사용한다. 세션 ID는 JSON Event Stream mode의 첫 `session` JSONL 이벤트 `id`에서 얻고, 완료/마지막 응답은 `turn_end` 또는 `agent_end` 이벤트의 assistant message에서 추출한다.
- 지속 데이터는 SQLite가 아닌 로컬 파일 기반 저장으로 관리한다.
- 작업 이력은 재시작 후 복원하지 않는 런타임 상태다.
- Codex CLI가 alpha 버전이므로 버전이 올라가면 Codex 전용 실행 계약을 재검증해야 한다.

## 4. 핵심 용어

### 워크스페이스

- 사용자가 작업 대상으로 선택한 폴더 경로다.
- 외부 실행기의 현재 작업 루트가 된다.
- 워크스페이스의 동일성은 폴더 path 값으로 판단하며, path가 같으면 동일한 워크스페이스다.
- 저장된 워크스페이스 목록에는 남을 수 있지만, 현재 열려 있는지는 런타임 상태로 따로 관리한다.

### 워크스페이스 탭

- 워크스페이스를 화면에 여는 최상위 탭이다.
- 하나의 워크스페이스 탭은 정확히 하나의 워크스페이스 폴더 path와 연결된다.
- 같은 워크스페이스 폴더 path에 대해 중복 워크스페이스 탭을 만들지 않고, 이미 열려 있으면 기존 탭을 활성화한다.
- 여러 워크스페이스 탭을 동시에 열 수 있다.
- 워크스페이스 탭의 표시 이름은 워크스페이스 path의 마지막 폴더명을 사용한다. 예: `c:\aaa\ccc`는 `ccc`로 표시한다.

### 세션 탭

- 워크스페이스 탭 내부에서 대화 흐름을 나누는 하위 탭이다.
- 세션 탭은 정확히 하나의 워크스페이스 탭에 소속된다.
- 하나의 워크스페이스 탭은 여러 세션 탭을 가질 수 있다.
- 세션 탭은 종류, 표시 이름, 세션 ID, 자동커밋 체크 상태, 열린 상태, 정렬 순서를 가진다.
- 세션 ID는 선택된 AI 실행기 실행으로 확정될 때까지 비어 있을 수 있다.
- 사용자는 세션 상단의 세션 ID 라벨을 클릭해 확정된 세션 ID를 클립보드에 복사할 수 있다.
- 세션 상단의 AI 실행기, 모델, 추론 레벨 선택값은 세션 탭의 런타임 실행 옵션으로 보관한다.
- 세션 상단의 AI 실행기, 모델, 추론 레벨 선택값은 설정의 기본 AI 설정으로 초기화한다. 이후 사용자가 세션에서 선택값을 바꾸면 워크스페이스 경로 단위로 마지막 선택값을 기억하며, 같은 워크스페이스에서 이후 새로 만드는 일반 세션과 프리셋 세션의 기본 선택값으로 사용한다.
- 작업이 등록되기 전의 열린 세션만 현재 설정에서 실행기 경로가 있는 AI 실행기 후보로 갱신된다.
- 작업이 등록되거나 프리셋 등록이 대기 상태가 되면 세션 상단 실행 옵션 컨트롤은 등록 시점 선택값 표시로 잠기며, 이후 설정 후보 변경으로 다른 provider를 표시하지 않는다.
- 일반 세션 탭은 `S<n>`, 프리셋 세션 탭은 `P<n>`, 프리셋 후보 세션 탭은 `P<n>-<m>` 표시 이름을 사용한다.

### 일반 세션 탭

- 사용자가 프롬프트 에디터에 직접 프롬프트를 입력해 작업을 등록하는 세션 탭이다.
- 표시 이름은 `S<n>` 형식을 사용한다.

### 프리셋 세션 탭

- j3aiPromptLoop 호환 프리셋을 실행해 후보 작업 세션을 생성하는 부모 세션 탭이다.
- 표시 이름은 `P<n>` 형식을 사용한다.
- 일반 세션 탭과 같은 번호 카운터를 공유한다.
- `Language`, `Instruction`, `Work Priority` 입력을 가진다.
- `Language`, `Instruction`, `Work Priority` 입력은 상단 한 줄에 표시한다.
- 하단에는 분석 프롬프트 상단에 추가할 여러 줄 prefix 편집 영역을 가진다.
- 등록줄은 `Auto Commit -> AI 실행기 -> model -> 추론레벨 -> 등록` 순서로 표시하고, 등록줄 실행 옵션은 세션 상단 실행 옵션과 독립된 후보 세션용 선택값으로 관리한다.
- 등록줄의 AI 실행기, 모델, 추론 레벨 선택값은 워크스페이스 경로 단위로 마지막 선택값을 기억하며, 같은 워크스페이스에서 이후 새로 만드는 프리셋 세션의 등록줄 기본 선택값으로 사용한다.
- prefix 편집 영역의 마지막 등록값은 워크스페이스 경로 단위로 기억하며, 같은 워크스페이스에서 이후 새로 만드는 프리셋 세션의 기본값으로 사용한다.
- 프리셋 세션의 분석 작업은 세션당 한 번만 등록할 수 있으며, 등록 대기 또는 등록 뒤에는 `Language`, `Instruction`, `Work Priority`, prefix 편집 영역, `Auto Commit`, 세션 상단과 등록줄의 실행 옵션, `등록` 버튼을 비활성화한다.

### 프리셋 후보 세션 탭

- 프리셋 세션의 분석 응답 `candidates`에서 생성된 후보 작업을 실행하는 세션 탭이다.
- 표시 이름은 부모 프리셋 세션 이름과 후보 순번을 조합한 `P<n>-<m>` 형식을 사용한다. 예: 부모가 `P2`이면 후보는 `P2-1`, `P2-2`처럼 표시한다.
- 후보 작업 프롬프트는 부모 프리셋 세션의 분석 응답 후보와 `<instruction>_work.md` 템플릿으로 생성한다.
- 후보 세션은 부모 프리셋 세션 탭 바로 오른쪽에 후보 순서대로 생성된다.

### 작업(Job)

- 사용자 또는 앱이 특정 세션 탭에 등록한 단일 프롬프트 실행 요청이다.
- 프리셋 흐름에서는 부모 프리셋 분석 프롬프트 작업, 후보 세션의 후보 작업 프롬프트, 자동커밋 작업도 모두 작업으로 취급한다.
- 작업은 등록 시점의 AI 실행기 provider와 실행 옵션 스냅샷을 가진다.
- 실행기 경로, timeout, 파일 로그 저장 같은 운영 설정은 작업 등록 시점에 고정하지 않고 실행 요청 생성 시점의 현재 설정과 작업 실행 옵션 스냅샷을 병합한다.
- 작업은 `대기`, `설정입력대기`, `실행 중`, `완료`, `실패`, `취소됨` 상태 중 하나를 가진다.
- 작업은 항상 하나의 세션 탭에 소속된다.

### AI 실행기(provider)

- 앱이 작업 실행을 위임하는 외부 Agent CLI다.
- provider 식별자는 `codex`, `claude_code`, `kilo_code`, `opencode`, `pi` 중 하나다.
- `codex`는 현재 기본 provider이며, 기존 Codex 실행 계약을 깨지 않고 유지한다.
- provider가 바뀌어도 워크스페이스 큐, 세션 탭, 프리셋 흐름, 로그 저장, timeout, 프로세스 트리 종료의 앱 공통 동작은 유지한다.

### 실행 계약

- 앱이 provider별 CLI를 같은 도메인 흐름에 연결하기 위해 정의하는 어댑터 기준이다.
- 실행 계약은 실행 명령, 작업 디렉터리 전달, 프롬프트 전달 방식, 세션 ID 획득, 세션 재개, stdout/stderr 파싱, 마지막 응답 추출, 모델/추론 옵션, 권한/자동 승인 정책, 버전 조회를 포함한다.
- 지원 목록에 있는 provider는 `ProviderAgentCliProcessRunner`가 선택된 provider adapter로 연결한다. 새 provider를 추가할 때 구현 전 검증이 끝나지 않은 provider는 계약을 문서화하되 실제 실행 경로로 연결하지 않는다.

### 워크스페이스 큐

- 각 워크스페이스 탭은 독립된 큐 시작/중지 상태를 가진다.
- 동시에 실행 가능한 작업은 워크스페이스별 최대 1개다.
- 서로 다른 워크스페이스의 시작된 큐는 동시에 실행될 수 있다.
- `시작`된 워크스페이스의 작업만 실행 후보가 된다.
- 특정 워크스페이스 큐가 중지되면 해당 워크스페이스의 실행 중 작업은 취소되고, 대기 작업은 유지된다.
- UI에서는 큐 시작과 큐 중지를 별도 버튼이 아니라 하나의 토글 버튼으로 제공한다.
- 왼쪽 사이드바의 `예약실행`은 현재 앱 실행 동안만 유지되는 전역 예약이다. 예약 시각은 로컬 시스템 시각 기준이며, 과거 또는 현재 시각은 입력할 수 없다.
- 예약 시각이 되면 그 순간 열린 워크스페이스 중 실제 실행 후보인 `대기` 작업이 있는 워크스페이스 큐만 시작한다. `설정 필요` 작업은 실행 준비가 필요하므로 예약 실행 대상에 포함하지 않는다.

### 세션

- 외부 실행기와 이어지는 대화 또는 실행 흐름의 식별자다.
- 세션 ID는 세션 탭 단위로 관리한다.
- 세션 ID는 세션 탭과 1:1로 매핑되며, 선택된 AI 실행기 실행으로 확정되기 전까지는 유보할 수 있다.
- 같은 세션은 현재 앱 실행 동안 여러 작업의 프롬프트/응답 이력을 시간순으로 가진다.

### 완료 세션

- 완료된 작업 결과를 `워크스페이스 path + 세션 ID` 기준으로 다시 모아 보여주는 읽기 전용 이력 묶음이다.

### 설정

- 앱 전역의 기본 동작을 결정하는 지속 데이터다.
- 예: 기본 워크스페이스 목록, 기본 AI 실행기 provider/model/추론 레벨, 외부 실행기 위치, 출력 글꼴 크기, 로그 파일 저장 사용 여부
- 모델과 추론 옵션은 설정 대화상자의 기본 AI 설정으로 저장할 수 있고, 세션 상단 또는 프리셋 등록줄의 드롭다운에서 직접 다시 선택할 수 있다. 작업 등록 시점의 선택값은 작업 실행 옵션으로 고정한다.
- 설정 대화상자에서는 출력 글꼴 크기, 기본 AI 실행기 provider/model/추론 레벨, 실행기 경로/디렉터리 또는 PATH 명령, 전체 실행 제한, 출력 무활동 제한, 종료 유예 시간, 파일 로그 저장 여부, UI 언어를 수정한다. 설정 대화상자는 상단 `상태`, 왼쪽 `기본 설정`/`실행 제한`, 오른쪽 `워크스페이스 기본 AI 설정` 구역으로 나누어 표시한다.
- provider 선택 후보는 화면에 `Codex CLI`, `Claude Code`, `Kilo Code`, `OpenCode`, `Pi Coding Agent`로 표시하고, 저장값은 각각 `codex`, `claude_code`, `kilo_code`, `opencode`, `pi`를 사용한다.
- 왼쪽 사이드바 하단의 설정 요약 라벨은 설정된 실행기 경로가 있는 사용 가능 AI 실행기 목록만 표시하고, 실행기 경로, 글꼴 크기, 파일 로그 상태 같은 다른 설정 항목은 표시하지 않는다.
- UI 언어는 `ko`와 `en`을 지원하며, 사용자는 화면에서 한국어와 English 중 하나를 선택한다.
- 설정 대화상자에는 앱 버전, 현재 설정된 AI 실행기의 버전 조회 결과, 앱 GPL-3.0 및 제3자 고지를 보여주는 `Licenses` 동작, 하단 GitHub 링크 `https://github.com/edgarp9`를 표시한다. `Licenses` 창에서는 외부 라이브러리와 리소스 목록인 `Notice Inventory`를 표시하고, 내부 배포 관리 섹션인 `Release Checklist`만 표시하지 않는다.
- About 창에는 상단에 `j3AITaskRunner <version>` 버전 라벨과 소스 코드 링크 `https://github.com/edgarp9`를 표시하고, 아래에는 배포물에 포함된 `about.txt` 내용을 읽기 전용으로 표시한다. `about.txt` 본문에는 앱 버전을 중복 명시하지 않는다. 별도의 `Licenses` 동작은 제공하지 않는다.
- 세션 상단 또는 프리셋 등록줄 드롭다운에서 모델을 `자동`으로 두면 선택된 provider의 기본 모델을 사용하고, 추론 옵션을 `자동`으로 두면 provider 또는 모델의 기본 추론 옵션을 사용한다.
- 설정 payload에는 기본 모델 `default_model`과 기본 추론 레벨 `default_reasoning_effort`를 저장한다. 레거시 설정 파일의 `model_reasoning_effort`는 `default_reasoning_effort`로 읽어 마이그레이션하며, 새로 저장하는 설정 payload에는 `model_reasoning_effort`를 포함하지 않는다.
- Codex provider의 현재 드롭다운 모델 후보는 `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5-codex`, `gpt-5.3-codex`, `gpt-5.2-codex`, `gpt-5.1-codex-max`, `gpt-5.1-codex`, `gpt-5.1-codex-mini`, `gpt-5.2`, `gpt-5.1`, `gpt-5`, `gpt-5-mini`, `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano`, `o4-mini`다.
- Codex provider의 현재 드롭다운 추론 레벨 후보는 `none`, `minimal`, `low`, `medium`, `high`, `xhigh`다.
- Claude Code, Kilo Code, OpenCode provider는 현재 고정 모델/추론 후보 목록을 제공하지 않고 `자동` 또는 기존 선택값 유지로 표시한다. Pi Coding Agent provider의 모델 후보는 고정하지 않고, 추론 후보는 문서화된 `off`, `minimal`, `low`, `medium`, `high`, `xhigh`를 표시한다. Codex 전용 모델 후보를 다른 provider에 강제로 노출하지 않는다.
- 디렉터리 설정 시 실행 파일 후보는 Codex provider가 `codex`, `codex.exe`, `codex-*`, `codex*.exe`, Claude Code provider가 `claude`, `claude.exe`, Kilo Code provider가 `kilo`, `kilo.exe`, OpenCode provider가 `opencode`, `opencode.exe`, Pi Coding Agent provider가 `pi`, `pi.exe`다.

## 5. 사용자 목표

사용자는 다음을 원한다.

- 여러 워크스페이스 탭을 동시에 열어 둘 수 있어야 한다.
- 각 워크스페이스 탭 안에서 여러 세션 탭을 만들고 독립된 작업 흐름을 유지할 수 있어야 한다.
- 사용자는 새 세션 버튼 오른쪽의 새 프리셋 버튼으로 프리셋 세션을 만들 수 있어야 한다.
- 사용자는 새 프리셋 버튼 오른쪽의 가져오기 버튼으로 여러 지시문을 한 번에 세션별 작업으로 등록할 수 있어야 한다.
- 사용자는 프리셋 세션에서 `Language`, `Instruction`, `Work Priority`를 입력해 j3aiPromptLoop 호환 프리셋 실행을 시작할 수 있어야 한다.
- 프리셋 실행이 끝나면 앱은 분석 응답 후보별 후보 세션과 후보 작업을 자동으로 만들어야 한다.
- 어떤 워크스페이스 탭의 어떤 세션 탭에서 어떤 작업이 실행 중인지 즉시 알 수 있어야 한다.
- 세션 상단 실행 상태는 현재 실행 중인 작업 ID와 함께 같은 세션에서 완료된 `job-N` 번호만 괄호 안에 표시해야 한다. 예: `실행 중: job-3 (1, 2)`.
- 세션이 실행 중이 아닐 때 상단 활동 문구는 `최근 작업`이 아니라 완료된 작업 목록을 `종료: 완료 job-1, 2, 3` 형식으로 표시해야 하며, `완료` 상태가 아닌 작업은 포함하지 않는다.
- 작업 등록 또는 가져오기로 새 대기 작업이 선택된 상태라면 상단 활동 문구는 `종료`가 아니라 `대기중: job-N` 형식으로 표시해야 한다. 같은 세션에 완료 작업이 있으면 `대기중: job-N (1, 2)`처럼 완료 번호를 괄호 안에 유지한다.
- 세션이 실행 중이 아니고 선택 또는 최근 작업에 사용자 메시지가 있으면 상단 활동 문구는 `종료` 라인에 합쳐 표시한다. 예: `종료: 작업을 취소했습니다. job-3 (1, 2)`.
- 선택 또는 최근 작업이 `실패` 상태이면 상단 활동 문구는 `종료: 실패 job-3 (1, 2) Reconnecting... 2/5 (request timed out)` 형식으로 표시하며, 실패 기본 접두어 `실행 실패:`는 반복 표시하지 않는다.
- 실행 로그를 실시간으로 보고, 워크스페이스 전체 작업 목록에서 실행 순서와 진행 상황을 확인할 수 있어야 한다.
- 사이드바/메인, 세션 탭/우측 작업 목록, 프롬프트/출력 영역 경계를 마우스로 드래그해 크기를 조절할 수 있어야 한다.
- 사용자는 왼쪽 사이드바를 좁은 버튼 바만 남기고 접어 메인 작업 영역을 넓히고, 접힌 상태에서도 보이는 버튼으로 다시 펼칠 수 있어야 한다.
- 가려진 워크스페이스 탭을 다시 표시해도 우측 작업 목록 내용의 요청 폭이 세션 탭 영역을 밀어내지 않아야 한다.
- 작업 목록 영역 너비가 줄거나 늘면 `순서`, `세션`, `상태`, `프롬프트` 컬럼은 기준 폭 비율을 유지하며 함께 조정되어야 한다.
- 프롬프트/출력 경계를 조절하면 프롬프트 섹션의 빈 여백이 아니라 프롬프트 에디터 자체의 높이가 함께 변해야 한다.
- 프롬프트 에디터 하단에는 별도 크기 조절 핸들을 두지 않고, 출력 영역 위의 프롬프트/출력 경계 바만 크기 조절에 사용해야 한다.
- 프롬프트 입력 편집 영역, 프리셋 프롬프트 상단 추가 내용 편집 영역, 가져오기 창 편집 영역은 우클릭 메뉴로 잘라내기, 복사, 붙여넣기, 모두 선택을 제공해야 한다.
- 세션 상단의 보조 메시지나 대기 사유가 없을 때는 빈 줄을 예약하지 않아 프롬프트 입력 영역이 상단 정보와 가깝게 보여야 한다.
- 작업이 실행될 때 어떤 AI 실행기 provider와 실행 설정이 적용되는지 세션 상단 선택값과 등록된 작업의 실행 옵션에서 알 수 있어야 한다.
- 사용자는 등록 버튼 왼쪽의 `자동커밋` 체크박스로 작업 등록 시 후속 커밋 요청을 자동 추가할지 선택할 수 있어야 한다.
- 사용자는 왼쪽 사이드바 하단의 설정 버튼으로 설정 대화상자를 열어 출력 글꼴 크기, AI 실행기 provider, 실행기 경로/디렉터리 또는 PATH 명령, 전체 실행 제한, 출력 무활동 제한, 종료 유예 시간, 파일 로그 저장 여부, UI 언어를 수정할 수 있어야 한다.
- 앱은 로그와 응답을 오래 읽는 흐름에 맞춰 기본 화면과 설정 대화상자에 다크 테마를 적용해야 한다.
- 앱은 실행 시 DPI 배율과 무관하게 메인 윈도우의 Tk client 영역을 1100 x 800 크기로 연다. OS 제목 표시줄과 테두리를 포함한 전체 창 캡처 크기는 이보다 클 수 있다.
- 왼쪽 사이드바는 실행 시 180 px 폭으로 연다.
- 워크스페이스 탭 내부의 오른쪽 작업 목록 영역은 실행 시 180 px 폭으로 열고, 세션 영역은 남은 폭을 사용한다.
- Windows에서는 Tk 루트 창을 만들기 전에 프로세스 DPI awareness를 설정해 고해상도/배율 환경에서 흐릿하게 보이지 않아야 한다.
- 이 앱은 목록, 탭, 버튼 중심의 관리 도구이므로 Windows DPI awareness는 `SYSTEM_AWARE` 우선 정책을 기본으로 사용해 모니터 이동 중 창 크기와 보이는 UI 양이 흔들리지 않아야 한다.
- Windows에서는 Tk 루트 창 생성 직후 실제 top-level frame HWND 기준 DPI를 우선 읽고, 실패하면 system DPI와 Tk `fpixels("1i")` 순서로 fallback해 `tk scaling`과 픽셀 기반 UI 값을 보정해야 한다.
- 서로 다른 배율의 모니터 사이로 이동하더라도 DPI 동기화는 root `<Configure>` 이벤트를 짧게 debounce한 뒤 1회만 수행하고, DPI callback에서 창 `geometry()`를 반복 재설정하지 않아야 한다.
- 앱 창과 릴리즈 실행 파일은 같은 체크리스트 아이콘을 사용해야 한다.
- 앱을 다시 열었을 때 이전 작업 이력이 초기화되어도 동작상 문제가 없어야 한다.
- 외부 실행기 경로/디렉터리 또는 PATH 명령이 잘못되었거나 워크스페이스가 유효하지 않은 경우, 왜 실행이 막혔는지 이해할 수 있어야 한다.
- 워크스페이스 작업 목록에서 작업을 우클릭하면 메뉴에서 프롬프트 미리보기를 확인하고 전체 프롬프트 창을 열 수 있어야 한다.
- 워크스페이스 작업 목록에서 작업을 우클릭해 삭제할 수 있어야 한다.
- 저장된 워크스페이스 목록에서 선택한 워크스페이스를 제거할 수 있어야 한다.
- 왼쪽 사이드바의 저장된 워크스페이스 영역에 폴더를 드래그앤드롭해 워크스페이스를 등록할 수 있어야 한다.
- 큐가 시작되어 있거나 작업이 실행 중인 동안에는 OS의 유휴 절전으로 작업이 끊기지 않아야 한다.

## 6. 도메인 규칙

### 구조 규칙

1. 워크스페이스 탭과 워크스페이스 폴더 path는 1대1이다.
2. 워크스페이스 동일성은 폴더 path 값이 같은지로 판단한다.
3. 같은 워크스페이스 폴더 path를 다시 열면 새 워크스페이스 탭을 만들지 않고 기존 탭을 활성화한다.
4. 여러 워크스페이스 탭을 동시에 열 수 있다.
5. 하나의 워크스페이스 탭은 여러 세션 탭을 가질 수 있다.
6. 세션 탭은 정확히 하나의 워크스페이스 탭에 속한다.
7. 작업은 정확히 하나의 세션 탭에 속하며, 실행 시 상위 워크스페이스 탭의 워크스페이스를 사용한다.
8. 세션 ID는 세션 탭과 1:1로 매핑된다.
9. 완료 세션은 `워크스페이스 path + 세션 ID` 조합으로 구분한다.
10. 워크스페이스 전체 작업 목록은 같은 워크스페이스 탭에 속한 모든 작업을 큐 순서 기준으로 표시하며, 일반 작업의 기본 큐 순서는 작업 등록 순서를 따른다.
11. 작업 식별자는 내부 선택/삭제에만 사용하고, 워크스페이스 작업 목록의 표시 컬럼으로는 보여주지 않는다.
12. 워크스페이스 전체 작업 목록의 `순서`, `세션`, `상태`, `프롬프트` 컬럼 폭은 작업 목록 영역의 실제 너비에 맞춰 기준 폭 비율대로 함께 조정한다.

### 프롬프트 자산 규칙

1. 프리셋 프롬프트 자산은 앱 기준 `prompt/<Language>/<instruction>.md`와 `<instruction>_work.md` 쌍으로 관리한다.
2. 패키징된 실행 환경에서는 앱 실행 파일 기준 `prompt`를 먼저 찾고, 없으면 `lib/prompt` 또는 PyInstaller 수집 루트의 `prompt`를 사용한다.
3. `Language`와 `instruction`은 단일 경로 세그먼트여야 하며 절대경로, 상위경로, 슬래시, 역슬래시, Windows 파일명 금지 문자, 제어 문자, 예약 장치명, 앞뒤 공백, 끝의 점 또는 공백을 포함하면 거부한다.
4. 유효한 instruction 목록에는 분석 프롬프트와 작업 프롬프트 템플릿 쌍이 모두 있는 항목만 포함한다.
5. 기본 제공 instruction 중 `de-abstraction`은 실제 사용 근거가 약한 인터페이스, wrapper, factory, strategy, manager, service, 단일 구현 다형성 등을 찾아 기존 동작을 유지하면서 더 직접적인 구조로 되돌리는 후보를 만든다.
6. 작업 프롬프트 템플릿은 `{{candidates_payload}}` 자리표시자를 분석 응답 후보 payload 문자열로 치환해 생성하며, 자리표시자가 없는 템플릿은 오류로 처리한다.
7. 분석 응답 JSON은 순수 JSON 객체, Markdown fenced code block 안의 JSON 객체, 본문 중 첫 JSON 객체를 허용한다.
8. 분석 응답의 최상위 `candidates`는 배열이어야 하며, 각 후보는 `id`, `title`, `problem`, `evidence`, `priority`, `risk`, `impact`를 가진다.
9. 후보 `id`는 분석 응답 안에서 중복될 수 없다.
10. `Work Priority`는 `high`, `medium`, `low` 중 하나이며 기본값은 `medium`이다. 해당 threshold 이상 후보만 작업 대상으로 선택한다. 우선순위 순서는 `high > medium > low`다.
11. 작업 프롬프트 생성 응답은 최상위 `prompts` 배열을 가진 JSON 객체여야 하며, 각 항목은 비어 있지 않은 `candidate_id`, `title`, `prompt`를 포함해야 한다.
12. `prompts` 항목 개수는 작업 대상으로 선택된 `candidates` 개수와 같아야 하며, 다르면 오류로 처리한다.
13. 각 `candidate_id`는 입력 후보 `id`와 매칭되어야 하며, 생성된 작업 프롬프트는 생성 응답 순서가 아니라 입력 후보 순서대로 후보 세션에 연결한다. 누락되거나 중복된 `candidate_id`, 알 수 없는 `candidate_id`, 빈 `prompt`는 오류로 처리한다.
14. 작업 프롬프트 템플릿은 AI 실행기에게 테스트나 검증을 watch mode가 아닌 one-shot 명령으로 실행하고, 종료되지 않는 dev server 명령을 사용하지 않도록 지시해야 한다. 서버 실행이 꼭 필요하면 timeout 가능한 방식으로 짧게 smoke 확인한 뒤 종료하도록 안내한다.
15. 분석 프롬프트와 작업 프롬프트 템플릿의 응답 형식 섹션은 각각 `candidates`와 `prompts` 최상위 JSON 객체 예시를 포함한다.
16. 이 프롬프트 규칙은 장기 실행을 줄이는 보조 장치다. 최종 보호 장치는 프로세스 runner의 `execution_timeout_minutes`와 `inactivity_timeout_minutes` 감시이며, 프롬프트 규칙이 지켜지지 않아도 runner timeout이 종료되지 않는 실행을 끊을 수 있어야 한다.

### 탭 이름 규칙

1. 워크스페이스 탭의 표시 이름은 워크스페이스 path의 마지막 폴더명을 사용한다.
2. 예를 들어 `c:\aaa\ccc` 또는 `/aaa/ccc` 워크스페이스는 `ccc`로 표시한다.
3. 마지막 폴더명을 얻을 수 없는 루트 경로는 정규화된 path를 표시 이름으로 사용한다.
4. 새 일반 세션 탭의 기본 표시 이름은 부모 워크스페이스 탭 안에서 `S<n>` 형식을 사용한다.
5. 새 프리셋 세션 탭의 기본 표시 이름은 부모 워크스페이스 탭 안에서 `P<n>` 형식을 사용한다.
6. 일반 세션 탭과 프리셋 세션 탭은 같은 워크스페이스 탭 안에서 하나의 번호 카운터를 공유한다.
7. 예를 들어 같은 워크스페이스 탭에 `S1`, `S2`가 이미 있으면 다음 프리셋 세션 탭은 `P3`으로 표시한다.
8. 프리셋 후보 세션 탭은 부모 프리셋 세션 이름과 후보 순번을 사용해 `P<n>-<m>` 형식으로 표시한다.
9. 프리셋 후보 세션 탭은 일반/프리셋 세션 탭의 공유 번호 카운터를 증가시키지 않는다.
10. 열린 프리셋 후보 세션 탭이 남아 있으면 공유 번호 카운터는 초기화하지 않는다.
11. 특정 워크스페이스 탭 안의 모든 세션 탭이 닫히면 그 워크스페이스에서 다음 새 일반 세션 탭 이름은 다시 `S1`, 다음 새 프리셋 세션 탭 이름은 다시 `P1`부터 시작한다.

### 실행 규칙

1. 각 워크스페이스 큐는 독립적으로 실행되며, 워크스페이스마다 동시에 실행되는 작업은 최대 1개다.
2. 서로 다른 워크스페이스의 시작된 큐는 각자의 실행 슬롯이 비어 있으면 동시에 작업을 실행할 수 있다.
3. 새 작업이 추가될 때 해당 워크스페이스에 실행 중 작업이 없고 큐가 시작 상태이면 즉시 실행할 수 있다.
4. 작업은 등록 시점의 AI 실행기 provider와 모델/추론 옵션 스냅샷을 고정한다.
5. 작업 실행 시 워크스페이스는 상위 워크스페이스 탭에서 가져오고, 실행기 경로, timeout, 파일 로그 같은 운영 설정은 프로그램 설정의 현재 값을 사용한다.
6. 작업 실행 시 세션 ID는 소속 세션 탭에서 가져오되, 아직 확정되지 않았으면 없는 상태로 시작할 수 있다.
7. `설정입력대기` 상태의 작업은 스케줄러 선택 대상에서 제외한다.
8. 같은 워크스페이스의 대기 및 설정입력대기 작업은 기본적으로 작업 등록 순서를 큐 순서로 유지한다.
9. 큐 표시 순서와 일반 작업 실행 후보 순서는 같은 기준을 사용하며, 세션 탭 종류나 세션 표시 이름만으로 작업을 재배치하지 않는다.
10. 한 워크스페이스에서 실행 중 작업이 끝나면 같은 워크스페이스 안에서 큐 순서가 가장 앞선 대기 작업을 선택한다.
11. 자동커밋 작업은 원 작업 직후 등록되므로 원 작업 바로 다음 큐 순서를 가진다.
12. 명시적 우선순위 재배치가 필요한 흐름은 등록 순서 예외로 취급하며, 재배치 요청에 포함된 작업 순서를 유지한다.
13. 큐가 중지되면 해당 워크스페이스에서 실행 중인 외부 프로세스를 종료하고, 해당 큐의 새 작업 선택은 중단한다.
14. 큐 토글 버튼은 중지 상태에서 누르면 큐 시작을 요청하고, 시작 또는 시작 요청 중 상태에서 다시 누르면 큐 중지를 요청한다.
15. 워크스페이스 작업 목록이 비어 있으면 큐 토글 버튼은 시작 요청을 만들지 않고 중지 상태를 유지한다.
16. 워크스페이스 작업 목록의 모든 작업이 `완료` 상태가 되면 해당 워크스페이스 큐는 자동으로 `중지` 상태가 된다.
17. 앱은 하나 이상의 큐가 시작 상태이거나 실행 중 작업이 남아 있으면 시스템 유휴 절전을 방지하고, 모든 큐와 실행 작업이 끝나면 절전 방지를 해제한다.
18. 절전 방지는 사용자가 직접 실행한 종료, 재시작, 덮개 닫기 강제 정책, 배터리 부족 절전 같은 OS 정책을 막는 기능으로 취급하지 않는다.
19. 프리셋 후보 세션의 후보 작업은 부모 프리셋 세션 작업이 완료된 직후 실행될 수 있도록 같은 워크스페이스 큐에서 우선순위를 가진다.
20. 프리셋 후보 세션 우선순위는 일반 작업 등록 순서보다 우선하는 명시적 예외이며, 후보 세션 생성 순서를 유지한다.
21. 후보 세션에 후보 작업 뒤 자동커밋 작업이 함께 등록된 경우, 해당 자동커밋 작업은 같은 후보 세션의 후보 작업 바로 다음에 실행되는 후속 작업으로 취급한다.

### 자동커밋 규칙

1. 일반 세션 탭의 프롬프트 입력 영역에는 등록 버튼 왼쪽에 `자동커밋` 체크박스를 표시한다.
2. `자동커밋` 체크박스는 새 일반 세션 UI와 새 프리셋 세션 UI에서 기본 체크 상태다.
3. 모든 세션 상단에는 `세션 닫기` 왼쪽에 `AI 실행기`, `model`, `추론레벨` 드롭다운을 표시한다. 세션 상단과 프리셋 등록줄의 실행 옵션 드롭다운은 남는 가로 폭을 채우지 않고 압축된 고정 문자 폭으로 왼쪽 정렬한다.
4. `AI 실행기` 드롭다운에는 설정에 실행기 경로 또는 PATH 명령이 설정된 provider만 표시한다.
5. AI 실행기를 선택하면 해당 provider의 모델 후보를 표시하고, 모델을 선택하면 해당 provider/model 기준 추론레벨 후보를 표시한다.
6. 작업을 등록하면 해당 세션의 AI 실행기/model/추론레벨 드롭다운을 잠그고, 등록된 작업은 그 선택값을 실행 옵션 스냅샷으로 가진다.
7. 일반 세션에서 체크된 상태로 사용자가 작업을 등록하면 앱은 사용자가 입력한 프롬프트 작업을 먼저 등록하고, 같은 세션 탭에 `커밋해 주세요.` 프롬프트 작업을 바로 다음 작업으로 추가한다.
8. 일반 세션에서 체크 해제 상태이면 사용자가 입력한 프롬프트 작업만 등록한다.
9. 프리셋 세션도 자동커밋 체크 상태를 가지지만, 부모 프리셋 분석 작업 완료 뒤에는 부모 프리셋 세션에 커밋 작업을 자동 등록하지 않는다.
10. 프리셋 후보 세션은 생성 시점의 부모 프리셋 세션 자동커밋 체크 상태와 등록줄 실행 옵션을 따른다.
11. 부모 프리셋 세션의 자동커밋이 체크되어 있으면 각 후보 세션에는 후보 작업 프롬프트 뒤에 `커밋해 주세요.` 프롬프트 작업을 등록한다.
12. 부모 프리셋 세션의 자동커밋이 체크 해제되어 있으면 각 후보 세션에는 후보 작업 프롬프트만 등록한다.
13. 프리셋 후보 세션과 작업을 자동 등록한 뒤 UI 상태 메시지와 워크스페이스 작업 목록은 실제 등록된 후보 세션 수와 작업 수를 반영한다.

### 프리셋 실행 규칙

1. 워크스페이스 탭의 세션 제어 영역에는 `새 세션` 버튼 오른쪽에 `새 프리셋`, 그 오른쪽에 `가져오기` 버튼을 둔다.
2. 사용자가 `새 프리셋`을 누르면 앱은 현재 워크스페이스 탭에 프리셋 세션 탭을 만들고 `P<n>` 이름을 부여한다.
3. 프리셋 세션 UI는 상단 한 줄의 `언어`, `지시문`, `우선순위` 입력, 하단 prefix 편집 영역, 자동커밋 체크박스를 제공한다. `우선순위`는 `high`, `medium`, `low` 중 하나를 선택하며 기본값은 `medium`이다. 세 입력 콤보박스는 패널의 남는 가로 폭을 채우지 않고 고정 문자 폭으로 왼쪽 정렬한다.
4. 사용자가 한 워크스페이스에서 프리셋 `언어`를 변경하면, 같은 워크스페이스에서 이후 새로 만드는 프리셋 세션은 해당 언어를 기본 선택값으로 사용한다. 해당 언어가 더 이상 프롬프트 자산 목록에 없으면 사용 가능한 첫 언어를 기본값으로 사용한다.
5. 사용자가 한 워크스페이스에서 프리셋 `지시문`을 변경하면, 같은 워크스페이스와 같은 언어에서 이후 새로 만드는 프리셋 세션은 해당 지시문을 기본 선택값으로 사용한다. 해당 지시문이 더 이상 선택한 언어의 프롬프트 자산 목록에 없으면 사용 가능한 첫 지시문을 기본값으로 사용한다.
6. 사용자가 한 워크스페이스에서 프리셋 `우선순위`를 변경하면, 같은 워크스페이스에서 이후 새로 만드는 프리셋 세션은 해당 우선순위를 기본 선택값으로 사용한다. 해당 우선순위가 더 이상 유효하지 않으면 `medium`을 기본값으로 사용한다.
7. 사용자가 한 워크스페이스에서 prefix 편집 영역을 입력하고 프리셋 작업 등록에 성공하면, 같은 워크스페이스에서 이후 새로 만드는 프리셋 세션은 해당 prefix를 기본값으로 사용한다. 빈 prefix로 등록에 성공하면 기억된 기본값을 지운다.
8. 사용자가 한 워크스페이스에서 세션 상단 AI 실행기/model/추론레벨을 변경하면, 같은 워크스페이스에서 이후 새로 만드는 일반 세션과 프리셋 세션은 해당 실행 옵션을 기본 선택값으로 사용한다.
9. 사용자가 한 워크스페이스에서 프리셋 등록줄 AI 실행기/model/추론레벨을 변경하면, 같은 워크스페이스에서 이후 새로 만드는 프리셋 세션은 해당 등록줄 실행 옵션을 기본 선택값으로 사용한다. 등록줄 실행 옵션은 세션 상단 실행 옵션과 별도로 기억한다.
10. 프리셋 세션 실행은 `prompt/<Language>/<instruction>.md` 분석 프롬프트 상단에 등록 시점 prefix를 붙인 프롬프트를 부모 프리셋 세션의 작업으로 등록해 실행한다. 빈 prefix이면 원본 분석 프롬프트만 사용하며, 실제 prompt 파일은 수정하지 않는다.
11. 프리셋 분석 작업을 한 번 등록한 프리셋 세션은 다시 등록할 수 없으며, 앱은 `Language`, `Instruction`, `Work Priority`, prefix 편집 영역, `Auto Commit`, `등록` 버튼을 비활성화한다.
12. 분석 프롬프트 실행이 완료되면 앱은 응답에서 후보 목록인 `candidates`를 추출한다.
13. 앱은 각 후보와 `Language`, `Instruction`, `Work Priority` 입력값을 사용해 대응하는 `<instruction>_work.md` 작업 프롬프트 템플릿으로 후보별 작업 프롬프트를 생성한다. 이 작업 프롬프트 생성 턴은 prefix를 다시 붙이지 않고, 분석 턴의 기존 세션을 resume하지 않으며, 선택 후보 payload만 포함한 새 AI 실행기 세션으로 실행하고 세션 상단 실행 옵션을 사용한다.
14. 앱은 부모 프리셋 세션 탭 바로 오른쪽에 후보 순서대로 `P<n>-1`, `P<n>-2` 형식의 프리셋 후보 세션 탭을 생성한다.
15. 앱은 각 프리셋 후보 세션에 생성된 후보 작업 프롬프트를 등록줄 실행 옵션으로 작업 등록한다.
16. 부모 프리셋 세션의 분석 작업이 완료되어 후보 세션이 생성되어도 부모 프리셋 세션에는 자동커밋 작업을 등록하지 않는다.
17. 후보 세션의 자동커밋 작업 등록 여부는 자동커밋 규칙에 따라 부모 프리셋 세션의 체크 상태로 결정한다.
18. 후보 세션에 등록된 후보 작업은 부모 프리셋 세션 작업 직후 실행될 수 있도록 스케줄러 우선순위를 가진다.

### 지시문 가져오기 규칙

1. 사용자가 `가져오기`를 누르면 앱은 메인 윈도우 기준 중앙에 모달 가져오기 창을 띄운다.
2. 가져오기 창은 여러 줄을 입력할 수 있는 `Text` 편집 영역과 하단 `등록` 버튼을 제공한다.
3. `Text` 편집 영역에는 기본 예시로 `step 1`, `step 2`를 담은 두 개의 Markdown ` ```text ` 코드 블록을 표시한다.
4. `Text` 편집 영역은 우클릭 메뉴로 잘라내기, 복사, 붙여넣기, 모두 선택을 제공한다.
5. 가져오기는 입력값에서 Markdown ` ```text ` 코드 블록만 추출한다.
6. ` ```text ` 코드 블록 하나는 새 일반 세션 탭 하나에 대응한다. 예를 들어 유효한 ` ```text ` 코드 블록이 3개이면 새 일반 세션도 3개 만든다.
7. 코드 블록 바깥의 텍스트는 가져오기 대상에서 제외한다.
8. 닫히지 않은 ` ```text ` 코드 블록, 유효한 코드 블록이 없는 입력, 내용이 비어 있는 입력은 모달 오류로 표시하고 세션과 작업을 만들지 않는다.
9. 각 새 일반 세션에는 코드 블록 내용을 프롬프트 작업으로 등록한다.
10. 가져오기 창의 자동커밋 체크 상태가 켜져 있으면 각 새 일반 세션의 가져온 프롬프트 작업 뒤에 `커밋해 주세요.` 자동커밋 작업을 등록한다.
11. 등록 뒤 UI 상태 메시지와 워크스페이스 작업 목록은 실제 생성된 세션 수와 작업 수를 반영한다.

### 상태 전이 규칙

1. 대표 상태 전이는 `대기 -> 설정입력대기 -> 실행 중 -> 완료/실패/취소됨`이다.
2. 실행 전 검증에서 막히지 않은 작업은 `대기 -> 실행 중`으로 바로 전이할 수 있다.
3. 큐 순서를 유지한 채 계속 기다리는 경우에는 `대기 -> 대기` 전이를 허용한다.
4. 사용자가 필요한 설정을 보완해 다시 실행할 때 즉시 시작 가능하면 `설정입력대기 -> 실행 중`으로 전이한다.
5. 사용자가 필요한 설정을 보완했지만 즉시 시작할 수 없는 경우에는 `설정입력대기 -> 대기`로 되돌린 뒤 큐 순서를 다시 부여할 수 있다.
6. 실행 중 작업은 `완료`, `실패`, `취소됨` 중 하나로만 종료한다.

### 작업 삭제 규칙

1. 작업 삭제는 현재 앱 실행 중의 런타임 작업 목록에서 작업을 제거하는 동작이다.
2. `실행 중` 작업은 외부 프로세스와 백그라운드 이벤트가 연결되어 있으므로 직접 삭제하지 않는다.
3. `대기`, `설정입력대기`, `완료`, `실패`, `취소됨` 작업은 삭제할 수 있다.
4. 삭제된 작업은 워크스페이스 작업 목록에서 제거한다.
5. 삭제된 작업의 작업별 진행 로그와 사용자 메시지 캐시는 함께 제거한다.
6. 완료 세션 이력은 세션 단위의 읽기 전용 런타임 이력이므로 작업 목록 삭제만으로 제거하지 않는다.

### 저장된 워크스페이스 삭제 규칙

1. 저장된 워크스페이스 삭제는 persistent saved workspace list에서 선택 항목을 제거하는 동작이다.
2. 실제 워크스페이스 폴더는 삭제하지 않는다.
3. 이미 열린 워크스페이스 탭, 세션 탭, 작업 상태는 닫거나 삭제하지 않는다.
4. 선택한 워크스페이스 path에 연결된 열린 워크스페이스 탭에 `실행 중` 작업이 없으면 확인 팝업 없이 즉시 삭제한다.
5. 선택한 워크스페이스 path에 연결된 열린 워크스페이스 탭에 `실행 중` 작업이 있으면 저장 목록 삭제 의향을 확인한다.
6. 삭제 후 저장된 워크스페이스 목록을 즉시 갱신하고 persistent data에 반영한다.

### 워크스페이스 등록 규칙

1. 왼쪽 사이드바의 저장된 워크스페이스 영역은 OS 파일 관리자에서 드래그한 폴더 path를 받을 수 있다.
2. 드롭된 path는 기존 워크스페이스 등록 흐름과 동일하게 처리한다.
3. 여러 path가 한 번에 드롭되면 각 path를 독립된 워크스페이스 등록 요청으로 처리한다.

### 시작 인자 워크스페이스 열기 규칙

1. 사용자는 `python main.py <workspace_path> [<workspace_path> ...]` 형식으로 앱 시작 시 열 워크스페이스 path를 0개 이상 전달할 수 있다.
2. 시작 인자 워크스페이스 path는 실행 cwd 기준으로 `Path(path).expanduser().resolve()`한 문자열을 워크스페이스 열기 요청에 사용한다.
3. 앱 창 생성과 기본 UI 초기화가 끝난 뒤 `after(0, ...)`로 시작 인자 워크스페이스 열기를 예약한다.
4. 여러 path가 전달되면 인자 순서대로 기존 백그라운드 워크스페이스 열기 흐름에 요청한다.
5. 유효하지 않은 path가 있어도 앱 시작을 중단하지 않고 기존 워크스페이스 오류 표시 흐름으로 사용자에게 알린다.

### 작업 목록 컨텍스트 메뉴 규칙

1. 워크스페이스 작업 목록에서 작업 행을 우클릭하면 해당 작업의 프롬프트 미리보기를 메뉴 항목으로 표시한다.
2. 프롬프트 미리보기 메뉴 항목을 누르면 작업 프롬프트 원문을 별도 모달 창으로 보여준다.
3. 프롬프트 창은 메인 윈도우 기준 중앙에 배치하고, 스크롤 가능한 멀티라인 텍스트 영역에 원문을 표시한다.
4. 프롬프트 창은 작업 상태를 변경하지 않고, 런타임 작업 목록에도 영향을 주지 않는다.
5. 같은 메뉴에서 삭제 기능도 함께 제공하되, 실행 중 작업 삭제 제한은 작업 삭제 규칙을 따른다.

### AI 실행기 공통 실행 계약

1. 앱의 자동 실행 경로는 선택된 provider의 비대화형 또는 스크립트용 명령을 사용한다. 대화형 TUI는 사용자가 직접 여는 화면이 아니므로 작업 큐 실행 경로로 사용하지 않는다.
2. 세션 탭에 세션 ID가 없으면 provider 계약의 최초 실행 명령을 사용하고, 세션 ID가 있으면 provider 계약의 세션 재개 명령을 사용한다.
3. 외부 실행기 프로세스의 `cwd`는 항상 워크스페이스 path로 지정한다. provider가 작업 디렉터리 옵션을 제공하면 같은 워크스페이스 path를 옵션에도 전달한다.
4. 프롬프트 전달 방식은 provider 계약에서 `stdin`, 위치 인자, `--prompt`, 파일 입력, stream-json 입력 중 하나로 명시한다.
5. 세션 ID 획득 방식은 provider별 stdout 이벤트, JSON 결과, 세션 목록 명령, 명시적 session-id 옵션 중 하나로 검증한다. 검증 전에는 해당 provider를 자동 실행 경로에 연결하지 않는다.
6. stdout은 provider 계약에 맞게 구조화 이벤트 또는 텍스트 로그로 파싱한다. stderr는 사용자 응답 소스가 아니라 경고와 진단 로그로 취급한다.
7. 마지막 응답 추출 방식은 provider 계약에서 파일 출력, JSON 결과 필드, 이벤트 스트림의 최종 메시지 중 하나로 고정한다.
8. 모델과 추론 옵션은 provider별 이름이 다를 수 있으므로 공통 UI 용어는 `model`과 `추론레벨`로 두고, 실제 CLI 인자는 provider 어댑터에서 변환한다.
9. 권한/자동 승인 정책은 provider별 플래그 의미가 다르므로 `기본`, `자동 승인`, `권한 우회` 같은 앱 정책 값을 provider 계약으로 매핑한다. 위험 플래그는 이름과 효과를 문서에 남긴 뒤 명시적으로 선택된 경우에만 사용한다.
10. 버전 조회는 provider 계약에 따라 실행하고, 설정 대화상자에는 선택된 AI 실행기의 버전 조회 결과를 표시한다.
11. 작업 객체는 도메인 상태로서 등록 시점의 provider와 실행 옵션 스냅샷을 소유하며, 디버깅과 재현을 위해 실제 적용된 provider, 실행 명령, 모델, 추론 옵션, 실행기 버전을 로그 아티팩트로 남길 수 있다.
12. 작업 큐, 세션 탭, 프리셋 흐름, 진행 로그, 파일 로그 저장, timeout 감시, 프로세스 트리 종료는 provider에 종속되지 않는 앱 공통 요구사항이다.
13. 파일 로그 저장 옵션이 켜져 있으면 앱은 provider별 stdout 원문, stderr 진단 로그, 프롬프트 원문, 실행 메타데이터를 파일 아티팩트로 저장한다.
14. `execution_timeout_minutes`가 `0`보다 크면 외부 프로세스 시작부터 완료까지의 전체 실행 시간을 제한하고, `0`이면 전체 실행 제한을 적용하지 않는다.
15. `inactivity_timeout_minutes`가 `0`보다 크면 stdout/stderr 라인 또는 provider별 이벤트가 없는 시간을 제한하고, `0`이면 무활동 제한을 적용하지 않는다.
16. 전체 실행 제한 또는 무활동 제한을 초과하면 앱은 외부 실행기에 정상 종료를 요청하고, 해당 작업은 사용자 취소가 아니라 `실패`로 판정한다.
17. timeout 또는 사용자 취소로 외부 실행기를 종료할 때는 가능한 플랫폼 기능으로 부모 프로세스뿐 아니라 프로세스 트리 종료를 시도한다.
18. `termination_grace_seconds`가 `0` 이상이면 정상 종료 요청 후 해당 초만큼 기다린 뒤 강제 종료를 시도한다. `0`이면 유예 없이 즉시 강제 종료를 시도한다.
19. timeout으로 정상 종료와 강제 종료를 시도한 뒤에도 외부 실행기 프로세스가 종료되지 않으면 앱은 무기한 대기하지 않고 실패 결과를 확정해 워크스페이스 실행 슬롯을 비운다.
20. timeout 회귀 스모크는 stdout 일부 출력 후 멈춤, 자식 프로세스를 만든 뒤 멈춤, stdout/stderr 무활동으로 멈춤 시나리오를 실제 fake 실행기로 재현하고, 각 실패 뒤 같은 워크스페이스의 다음 작업이 실행되는지 확인한다.

### Codex 전용 계약

1. `codex`는 현재 기본 provider이며, 기존 자동 실행 동작을 보존한다.
2. 앱의 Codex 자동 실행 경로는 기본 대화형 `codex`가 아니라 비대화형 `codex exec --json` 경로를 사용한다.
3. 세션 탭에 세션 ID가 없으면 최초 실행으로 보고 `codex exec --json`을 사용한다.
4. 세션 탭에 세션 ID가 있으면 후속 실행으로 보고 `codex exec resume --json <session_id>`를 사용한다.
5. 최초 실행에서는 워크스페이스 path를 `-C <workspace>`로 넘기고, 외부 실행기 프로세스의 `cwd`도 같은 워크스페이스 path로 지정한다.
6. 자동 실행 경로는 Git trusted directory 검사에 막히지 않도록 `--skip-git-repo-check`를 포함한다.
7. 자동 실행 경로는 지속 세션을 유지해야 하므로 `--ephemeral`을 사용하지 않는다.
8. 프롬프트는 quoting 차이와 인자 escaping 문제를 줄이기 위해 기본적으로 표준 입력(stdin)으로만 전달한다.
9. 모델은 `-m <model>`로, 추론 레벨은 `-c 'model_reasoning_effort=\"<level>\"'`로 반영한다.
10. 완료 응답 확보는 `-o` 또는 `--output-last-message`로 저장한 마지막 응답 파일을 기준으로 한다.
11. 표준 출력은 JSONL 이벤트 스트림으로 파싱하고, 표준 오류는 내부 진단 로그로만 취급한다.
12. 세션 ID는 `thread.started.thread_id` 또는 `codex.thread.started.thread_id`가 확인된 시점에만 세션 탭에 기록한다.
13. 성공 판정은 정상 종료만으로 충분하지 않고, `turn.completed` 또는 `codex.turn.completed` 확인과 마지막 응답 파일 확인까지 포함해야 한다.
14. 실패 판정은 `turn.failed`/`codex.turn.failed` 이벤트, 프로세스 시작 실패, 사용자 취소가 아닌 비정상 종료, `turn.completed` 없이 끝난 `error`/`codex.error` 이벤트를 포함한다.
15. `error`/`codex.error` 이벤트가 있더라도 정상 종료, `turn.completed`, 마지막 응답 파일이 모두 확인되면 해당 이벤트는 중간 재연결/진단 이벤트로 기록하고 작업은 성공으로 판정한다.
16. 재개 실행에서는 Codex CLI가 저장된 세션의 작업 루트를 재사용하지만, 앱은 프로세스 `cwd`를 현재 워크스페이스 path로 유지해 실행 전후 진단 기준이 흔들리지 않게 한다.
17. 앱은 Codex 세션 파일 저장소를 훑어 세션 cwd를 추론하지 않는다. 세션 탭과 워크스페이스 탭의 연결이 실행 요청의 기준이다.
18. 버전 조회는 설정된 실행기 명령에 `--version`을 전달해 확인한다.

권장 실행 형태:

```text
초기 실행:
codex.exe exec --json --skip-git-repo-check -C <workspace> -m <model> -c 'model_reasoning_effort="<level>"' -o <last_message_file> -

재개 실행:
codex.exe exec resume --json --skip-git-repo-check <session_id> -m <model> -c 'model_reasoning_effort="<level>"' -o <last_message_file> -
```

### Claude Code 계약

공식 문서 확인 기준 URL: https://code.claude.com/docs/en/cli-reference

구현 상태:

1. 자동 실행 명령은 interactive REPL이 아니라 `claude -p` print mode를 사용한다.
2. 작업 디렉터리는 subprocess `cwd=<workspace>`로 지정한다. `--add-dir`은 기본 명령에 포함하지 않는다.
3. stdout은 공식 문서 기준 `--output-format stream-json`을 사용하며, 문서상 stream-json에는 `--verbose`가 필요하므로 `--verbose`를 함께 전달한다. 진행 토큰/이벤트를 받을 수 있도록 `--include-partial-messages`도 함께 전달한다.
4. 세션 재개는 세션 탭에 확정된 세션 ID가 있을 때 `--resume <session_id>`를 사용한다. 단축형 `-r`은 같은 의미지만 adapter command에서는 명시형을 사용한다.
5. 모델 선택값이 있으면 `--model <model>`로 전달한다. 추론 선택값은 Claude Code 명령에는 적용하지 않는다.
6. 공식 문서는 print mode의 stdin 입력을 문서화하지만, 2026-05-27 로컬 환경에서는 `claude`가 PATH에 없어 `claude --help`와 `claude -p --help` 비교를 수행하지 못했다. 따라서 현재 adapter는 문서화된 prompt argument 형태를 사용한다. subprocess argv를 사용하므로 shell quoting은 피하지만, 매우 긴 프롬프트는 Windows command line 길이 제한 위험이 있어 launch metadata와 로그에 남긴다.
7. 최종 응답은 stream-json `result` 이벤트의 `result` 텍스트를 우선 사용하고, assistant content 이벤트는 보조 응답 텍스트로만 보존한다.
8. 세션 ID는 stream-json 이벤트의 `session_id`에서만 얻고, `~/.claude` 같은 로컬 세션 저장소를 훑어 추론하지 않는다.
9. 성공 판정은 정상 종료만으로 충분하지 않고, `result` 이벤트의 `subtype=success`와 마지막 응답 텍스트 확인까지 포함한다. 실패 판정은 `result`의 error subtype, 프로세스 시작 실패, 사용자 취소가 아닌 비정상 종료, 명확한 완료 이벤트 없이 끝난 실행을 포함한다.
10. `--dangerously-skip-permissions`, `bypassPermissions` 계열 권한 우회 플래그는 기본 명령에 포함하지 않는다. 현재 UI/문서화된 명시 설정이 없으므로 권한/자동 승인 옵션은 안전 기본값에 맡긴다.
11. 버전 조회는 설정된 실행기 명령에 `--version`을 전달해 확인한다.

권장 실행 형태:

```text
초기 실행:
claude.exe -p <prompt> --output-format stream-json --verbose --include-partial-messages --model <model>

재개 실행:
claude.exe -p <prompt> --output-format stream-json --verbose --include-partial-messages --resume <session_id> --model <model>
```

### Kilo Code 계약

공식 문서 확인 기준 URL: https://kilo.ai/docs/code-with-ai/platforms/cli-reference

구현 상태:

1. 자동 실행 명령은 `kilo run`을 사용한다.
2. 작업 디렉터리는 `--dir <workspace>`와 subprocess `cwd=<workspace>`를 함께 지정한다.
3. 세션 재개는 세션 탭에 확정된 세션 ID가 있을 때 `--session <session_id>`를 사용한다.
4. JSON 출력은 `--format json`을 사용하고, stdout raw JSON events를 provider adapter에서 공통 `AgentStreamEvent`로 변환한다.
5. 작업 실행 옵션의 모델 선택값이 있으면 `--model <provider/model>`로 전달하고, 추론/variant 선택값이 있으면 `--variant <value>`로 전달한다.
6. `--dangerously-skip-permissions`와 `--auto`는 기본 활성화하지 않는다. 현재 UI/문서화된 명시 설정이 없으므로 실행 명령에 포함하지 않는다.
7. 공식 문서는 `kilo run [message]` 위치 인자를 문서화하지만 stdin prompt 전달은 확인되지 않았다. 따라서 프롬프트는 subprocess argv의 마지막 위치 인자로 전달한다. shell을 거치지 않아 일반 quoting 문제는 줄이지만, 매우 긴 프롬프트는 Windows command line 길이 제한 위험이 있어 launch metadata와 로그에 남긴다.
8. 최종 응답은 stdout JSON 이벤트의 assistant/message/response 텍스트와 OpenCode 계열 `text` 이벤트의 `part.text`에서 추출한다. OpenCode 계열 `step_start`는 세션 시작, `step_finish`의 `reason=stop` 또는 완료성 reason은 완료 신호, `reason=tool-calls`는 중간 진행 신호로 변환한다. 실제 CLI JSON schema가 바뀔 수 있으므로 기본 테스트는 fake executable/Popen 계약으로 검증하고, 실제 CLI 스모크는 별도 opt-in으로만 수행한다.

공식 문서 확인 필요 항목:

1. `kilo run`의 위치 인자, `--format default|json`, `--dir`, `--attach`의 최신 동작.
2. `--session`, `--continue`, `--fork`, `--cloud-fork`의 세션 재개/분기 규칙.
3. `--model provider/model`, `--variant`의 모델/추론 옵션 매핑.
4. `--dangerously-skip-permissions`, `--auto`의 권한/자동 승인 의미.
5. `--version`의 버전 조회 출력 형식.

### OpenCode 계약

공식 문서 확인 기준 URL: https://open-code.ai/en/docs/cli

구현 상태:

1. 자동 실행 명령은 `opencode run`을 사용한다.
2. 작업 디렉터리는 `--dir <workspace>`와 subprocess `cwd=<workspace>`를 함께 지정한다.
3. 세션 재개는 세션 탭에 확정된 세션 ID가 있을 때 `--session <session_id>`를 사용한다.
4. JSON 출력은 `--format json`을 사용하고, stdout raw JSON events를 provider adapter에서 공통 `AgentStreamEvent`로 변환한다.
5. 작업 실행 옵션의 모델 선택값이 있으면 `--model <provider/model>`로 전달하고, 추론/variant 선택값이 있으면 `--variant <value>`로 전달한다.
6. `--dangerously-skip-permissions`는 기본 활성화하지 않는다. 현재 UI/문서화된 명시 설정이 없으므로 실행 명령에 포함하지 않는다.
7. 공식 문서는 `opencode run [message..]` 위치 인자를 문서화하지만 stdin prompt 전달은 확인되지 않았다. 따라서 프롬프트는 subprocess argv의 마지막 위치 인자로 전달한다. shell을 거치지 않아 일반 quoting 문제는 줄이지만, 매우 긴 프롬프트는 Windows command line 길이 제한 위험이 있어 launch metadata와 로그에 남긴다.
8. 최종 응답은 stdout JSON 이벤트의 assistant/message/response 텍스트와 OpenCode 계열 `text` 이벤트의 `part.text`에서 추출한다. OpenCode 계열 `step_start`는 세션 시작, `step_finish`의 `reason=stop` 또는 완료성 reason은 완료 신호, `reason=tool-calls`는 중간 진행 신호로 변환한다. 실제 CLI JSON schema가 바뀔 수 있으므로 기본 테스트는 fake executable/Popen 계약으로 검증하고, 실제 CLI 스모크는 별도 opt-in으로만 수행한다.

공식 문서 확인 필요 항목:

1. `opencode run`의 non-interactive mode, `--format`, `--dir`, `--attach`의 최신 동작.
2. `--session`, `--continue`, `--fork`의 세션 재개/분기 규칙.
3. `--model provider/model`, `--variant`의 모델/추론 옵션 매핑.
4. `--dangerously-skip-permissions`, `OPENCODE_PERMISSION`의 권한/자동 승인 의미.
5. `--version` 또는 `-v`의 버전 조회 출력 형식.

### Pi Coding Agent 계약

공식 문서 확인 기준 URL: https://pi.dev/docs/latest/usage, https://pi.dev/docs/latest/json

구현 상태:

1. 자동 실행 명령은 interactive mode나 print mode가 아니라 JSON Event Stream mode인 `pi --mode json`을 사용한다.
2. 작업 디렉터리는 subprocess `cwd=<workspace>`로 지정한다. Pi CLI에는 OpenCode 계열의 `--dir`을 전달하지 않는다.
3. 세션 재개는 세션 탭에 확정된 세션 ID가 있을 때 `--session <session_id>`를 사용한다. `--continue`, `--resume`, `--fork`는 자동 실행 기본 명령에 포함하지 않는다.
4. stdout은 한 줄당 JSON 객체인 JSON Event Stream으로 읽고, 첫 `session` 이벤트의 `id`를 앱 세션 ID로 저장한다.
5. 작업 실행 옵션의 모델 선택값이 있으면 `--model <model>`로 전달한다. 추론 선택값이 있으면 `--thinking <level>`로 전달하고, Codex 호환 선택값 `none`은 Pi 문서의 `off`로 변환한다.
6. 프롬프트는 공식 문서의 `pi --mode json "Your prompt"` 형태에 맞춰 subprocess argv의 마지막 위치 인자로 전달한다. shell을 거치지 않아 일반 quoting 문제는 줄이지만, 매우 긴 프롬프트는 Windows command line 길이 제한 위험이 있어 launch metadata와 로그에 남긴다.
7. 최종 응답은 `turn_end`, `message_end`, `agent_end` 이벤트의 assistant message 텍스트에서 추출한다. `agent_end` 또는 `turn_end`를 명확한 완료 신호로 취급하고, 프로세스 정상 종료만으로 성공 처리하지 않는다.
8. 기본 명령에는 `--tools`, `--no-tools`, 권한/자동 승인 성격의 별도 옵션을 포함하지 않는다. Pi의 도구/권한 정책은 현재 Pi 설정과 사용자가 지정한 시스템 설정에 맡긴다.
9. 버전 조회는 설정된 실행기 명령에 `--version`을 전달해 확인한다.

권장 실행 형태:

```text
초기 실행:
pi.exe --mode json --model <model> --thinking <level> <prompt>

재개 실행:
pi.exe --mode json --session <session_id> --model <model> --thinking <level> <prompt>
```

### Provider별 구현 상태

1. Codex provider는 기본 provider이며 `CodexCliAdapter`와 `CodexCliProcessRunner` 호환 wrapper를 유지한다. 앱 계층은 `AgentRunRequest`, `AgentRunResult`, `AgentRunStatus`, `AgentStreamEvent`, `AgentParseSummary` 같은 공통 계약에만 의존한다.
2. Claude Code provider는 `ClaudeCodeCliAdapter`로 연결되어 stream-json stdout을 공통 이벤트와 마지막 응답으로 변환한다. 기본 명령에는 권한 우회 플래그를 포함하지 않는다.
3. Kilo Code provider는 OpenCode 계열 `run --format json` 계약을 공유하되 provider id와 실행 파일 후보를 `kilo_code`/`kilo`로 분리한다. 기본 명령에는 `--auto`나 권한 우회 플래그를 포함하지 않는다.
4. OpenCode provider는 OpenCode 계열 `run --format json --dir <workspace>` 계약으로 연결한다. 기본 명령에는 권한 우회 플래그를 포함하지 않는다.
5. Pi Coding Agent provider는 `PiCliAdapter`로 연결되어 JSON Event Stream stdout을 공통 이벤트와 마지막 응답으로 변환한다. 기본 명령에는 Pi 도구 allowlist/denylist 옵션을 포함하지 않는다.
6. 실제 CLI가 설치되지 않아도 기본 테스트가 통과해야 하며, 실제 CLI smoke는 `J3AITASKRUNNER_RUN_REAL_AGENT_SMOKE=1` 같은 명시 opt-in 조건에서만 실행한다.

### 재현/회귀 테스트 색인

아래 테스트는 구현 계약이 문서와 달라질 때 우선 확인할 회귀 지점이다.

1. Provider 명령 계약은 `tests/test_process_runner.py`의 `test_build_codex_command_for_initial_execution`, `test_build_codex_command_for_resume_execution`, `test_build_opencode_command_for_initial_execution`, `test_build_kilo_command_for_session_resume`, `test_build_claude_code_command_for_initial_execution`, `test_build_claude_code_command_for_session_resume`, `test_build_pi_command_for_initial_execution`, `test_build_pi_command_for_session_resume`에서 fake path/argv 기준으로 재현한다.
2. Provider stdout 파싱과 완료/실패 판정은 `tests/test_process_runner.py`의 `test_parser_accepts_codex_prefixed_session_and_turn_events`, `test_run_allows_transient_error_when_turn_completed`, `test_run_marks_failed_on_error_without_completed_turn`, `test_opencode_run_marks_success_with_json_response_and_workspace_cwd`, `test_kilo_run_marks_failed_on_failure_event`, `test_claude_run_marks_success_with_stream_json_result_and_workspace_cwd`, `test_claude_run_marks_failed_on_result_error`, `test_pi_run_marks_success_with_json_event_stream_and_workspace_cwd`, `test_pi_run_marks_failed_without_completion`에서 확인한다.
3. Timeout/cancel 정책은 `tests/test_process_runner.py`의 `test_cancel_terminates_running_process_and_marks_result_canceled`, `test_wait_returns_failed_when_execution_timeout_expires`, `test_wait_returns_failed_when_inactivity_timeout_expires`, `test_cancel_remains_distinct_even_when_timeout_settings_are_enabled`, `test_timeout_wait_returns_failed_when_process_ignores_termination`과 `tests/test_controller.py`의 `test_execution_timeout_fails_job_and_dispatches_next_queued_job`, `test_inactivity_timeout_message_is_distinct_from_execution_timeout_and_cancel`, `test_user_cancel_marks_job_canceled_not_failed`에서 확인한다.
4. 실제 subprocess 기반 재현은 `tests/test_timeout_smoke.py`의 `test_partial_stdout_hang_times_out_and_queue_continues`, `test_child_process_hang_times_out_and_queue_continues`, `test_quiet_hang_times_out_on_inactivity_and_queue_continues`를 사용한다. fake Codex 실행기가 일부 stdout 후 hang, 자식 프로세스 생성 후 hang, stdout/stderr 무활동 hang을 만들고, 실패 뒤 같은 워크스페이스의 후속 작업이 완료되는지 검증한다.
5. 설정 migration은 `tests/test_persistence.py`의 `test_combined_payload_loads_legacy_progress_logging_key`, `test_combined_payload_preserves_provider_executable_paths`, `test_combined_payload_migrates_legacy_executable_path_to_current_provider`, `test_combined_payload_missing_agent_provider_uses_codex_default`, `test_unknown_agent_provider_falls_back_to_codex_default`, `test_combined_payload_missing_execution_control_settings_uses_defaults`, `test_execution_control_settings_preserve_zero_and_reject_negative_values`에서 확인한다.
6. 파일 로그 저장 옵션과 UI 진행 로그 분리는 `tests/test_process_runner.py::test_run_with_file_logging_disabled_keeps_ui_parsing_without_log_artifacts`, `tests/test_execution_worker.py::test_disabled_file_logging_keeps_progress_log_event_and_session_id`, `tests/test_app_runtime.py::test_disabled_file_logging_keeps_ui_progress_log_events`에서 확인한다.

### 세션 규칙

1. 세션 탭은 첫 AI 실행기 실행 전까지 세션 ID가 비어 있을 수 있다.
2. 세션 ID는 선택된 provider 계약에서 정의한 방식으로 확인된 경우에만 세션 탭에 기록한다. Codex provider는 표준 출력 JSONL의 `thread.started.thread_id` 또는 `codex.thread.started.thread_id`를 사용한다.
3. 이미 세션 ID가 있는 세션 탭은 이후 작업에서도 같은 세션 ID를 이어서 사용한다.
4. 작업이 `실행 중` 상태가 되면 세션 이력에 해당 턴을 만들고 `Prompt:`를 즉시 표시한다.
5. 작업 완료 응답이 확정되면 같은 세션 이력 턴에 `Response:`를 채운다.
6. 세션 ID가 아직 확정되지 않은 최초 실행 중에도 세션 탭 기준 이력에는 시작된 턴을 표시할 수 있다.
7. 완료 이력은 현재 앱 실행 동안 `프롬프트`, `응답`, `시작 시각`, `완료 시각`, `마지막 활동 시각`을 보존한다.
8. 완료 세션 이력은 같은 워크스페이스 안에서 최근 활동 순으로 정렬한다.
9. 앱 종료 후에는 작업 이력과 세션 이력을 복원하지 않는다.

### 탭 종료 규칙

1. 탭 닫기는 삭제가 아니라 런타임의 `열림` 상태를 `닫힘` 상태로 바꾸는 동작이다.
2. 닫으려는 세션 탭 또는 워크스페이스 탭 범위 안에 `대기` 또는 `설정입력대기` 작업이 있으면 앱은 사용자에게 삭제 의향을 확인한다.
3. 사용자가 확인하면 닫는 범위 안의 `대기` 및 `설정입력대기` 작업은 스케줄러에서 삭제한다.
4. 사용자가 거부하면 탭을 닫지 않고 대기 작업도 삭제하지 않는다.
5. 닫으려는 세션 탭 또는 워크스페이스 탭 범위 안에 `실행 중` 작업이 있으면 앱은 사용자에게 취소 의향을 확인하고, 확인 시 외부 실행기를 명시적으로 종료한다.
6. 위 종료가 사용자 탭 닫기 동작으로 발생했으면 해당 실행 중 작업 상태는 `취소됨`으로 저장한다.
7. 실행 중 작업이 있는 세션 탭을 닫으면 해당 워크스페이스 큐 상태를 `중지`로 전환한다.
8. 워크스페이스 탭을 닫으면 해당 워크스페이스 큐 상태도 `중지`로 전환한다.

### 오류 규칙

1. 외부 실행기를 찾지 못하거나 워크스페이스 path가 유효하지 않으면 작업은 실패 처리보다 `설정입력대기` 상태로 전환한다.
2. 외부 실행기 path와 워크스페이스 path는 프로세스 시작 전에 앱에서 선검증한다.
3. 선택된 provider 계약의 실패 신호가 확인되면 작업은 `실패`다. Codex provider는 표준 출력 JSONL의 `turn.failed`/`codex.turn.failed` 이벤트를 실패 신호로 사용한다.
4. provider별 오류 이벤트 또는 오류 결과는 완료 신호와 마지막 응답이 확인되지 않았을 때 실패 근거로 사용하고, 성공 증거가 모두 있으면 진단 이벤트로만 기록한다. Codex provider는 `error`/`codex.error` 이벤트에 이 규칙을 적용한다.
5. 표준 오류에 경고, HTML, 플러그인 동기화 로그 같은 진단 텍스트가 출력되더라도 그것만으로 실패를 판정하지 않는다.
6. 앱이 사용자 취소로 외부 프로세스를 명시적으로 종료한 경우에만 작업을 `취소됨`으로 처리한다.
7. 사용자 취소가 아닌 비정상 종료는 `실패`로 처리한다.
8. 전체 실행 제한 또는 무활동 제한 초과로 외부 프로세스를 종료한 경우는 `실패`로 처리한다.
9. `설정입력대기` 상태에서는 사용자에게 필요한 설정 입력 또는 경로 수정을 안내하고, 해당 작업은 큐에서 제거하지 않는다.
10. 사용자가 필요한 설정을 보완해 다시 실행하면 즉시 시작 가능할 때는 `실행 중`, 즉시 시작할 수 없을 때는 `대기`로 전환한다.
11. 내부 예외 원인은 로그로 남기고, UI에는 이해 가능한 메시지로 변환해 보여준다.
12. 프리셋 세션 등록 시 `Language`, `Instruction`, `Work Priority`가 비어 있거나 분석 프롬프트와 작업 프롬프트 템플릿 쌍을 찾지 못하면 모달 오류를 표시하고 작업을 등록하지 않는다.
13. 프리셋 분석 응답에서 유효한 `candidates`를 추출할 수 없으면 부모 프리셋 작업을 `실패`로 처리하고 후보 세션을 만들지 않는다.

## 7. 주요 유스케이스

### 유스케이스 1: 앱 시작

1. 앱이 시작되면 저장된 설정과 워크스페이스 목록을 읽는다.
2. 저장 대상이 아닌 워크스페이스 탭, 세션 탭, 작업 이력은 새 런타임 기준으로 초기화한다.
3. 사용자는 저장된 목록에서 하나 이상의 워크스페이스를 선택해 동시에 열 수 있다.
4. 사용자는 저장된 목록에서 선택한 워크스페이스를 제거할 수 있으며, 이 동작은 실제 폴더나 열린 탭을 삭제하지 않는다.

### 유스케이스 2: 워크스페이스 탭 열기

1. 사용자가 저장된 워크스페이스를 선택하거나 새 폴더를 추가한다.
2. 같은 워크스페이스 폴더 path가 이미 열려 있으면 기존 워크스페이스 탭을 활성화한다.
3. 아직 열려 있지 않으면 새 워크스페이스 탭을 만들고 표시 이름을 워크스페이스 path의 마지막 폴더명으로 부여한다.
4. 새 워크스페이스 탭 안에서 하나 이상의 세션 탭을 만들 수 있다.

### 유스케이스 3: 일반 세션 탭 만들기

1. 사용자가 특정 워크스페이스 탭 안에서 `새 세션`을 누른다.
2. 앱은 부모 워크스페이스 탭과 연결된 세션 탭 상태를 생성하고 기본 표시 이름을 `S<n>` 규칙에 따라 부여한다.
3. 이후 해당 세션 탭에서 독립적으로 작업을 등록할 수 있다.

### 유스케이스 4: 프리셋 세션 탭 만들기

1. 사용자가 특정 워크스페이스 탭 안에서 `새 세션` 버튼 오른쪽의 `새 프리셋`을 누른다.
2. 앱은 부모 워크스페이스 탭과 연결된 프리셋 세션 탭 상태를 생성하고 기본 표시 이름을 `P<n>` 규칙에 따라 부여한다.
3. 일반 세션 `S1`, `S2`가 이미 있으면 새 프리셋 세션은 같은 번호 카운터를 공유해 `P3`이 된다.
4. 앱은 프리셋 세션 탭에 상단 한 줄의 `Language`, `Instruction`, `Work Priority` 입력, 하단 prefix 편집 영역, 자동커밋 체크박스를 표시한다.

### 유스케이스 5: 프리셋 세션 실행

1. 사용자가 프리셋 세션 탭에서 `Language`, `Instruction`, `Work Priority`를 입력하고 프리셋 실행을 요청한다.
2. 앱은 `prompt/<Language>/<instruction>.md` 분석 프롬프트 상단에 등록 시점 prefix를 붙인 프롬프트를 만들고 부모 프리셋 세션에 분석 작업으로 등록한다.
3. 분석 작업 등록이 성공하면 해당 프리셋 세션의 `Language`, `Instruction`, `Work Priority`, prefix 편집 영역, `Auto Commit`, 세션 상단과 등록줄의 실행 옵션, `등록` 버튼은 비활성화되고 같은 프리셋 세션에서 두 번째 분석 작업 등록은 거부된다.
4. 분석 작업이 완료되면 앱은 응답에서 `candidates`를 추출한다.
5. 분석 응답이 비어 있거나 `candidates` 계약을 만족하지 못하면 앱은 턴2를 시작하지 않고 내부 로그를 남긴 뒤 해당 워크스페이스 큐를 중지한다. 단, `{"candidates": []}`처럼 후보가 0건인 정상 JSON 응답은 오류가 아니며 큐 중지 없이 프리셋 후속 단계를 종료한다.
6. 앱은 `Work Priority` threshold에 맞는 후보만 선택한다.
7. 선택 후보가 없으면 후보 세션과 후보 작업을 만들지 않고 프리셋 실행 후속 단계를 종료한다.
8. 앱은 선택 후보 payload를 JSON 문자열로 직렬화하고 `<instruction>_work.md` 템플릿의 `{{candidates_payload}}` 자리표시자를 치환한다.
9. 앱은 치환된 템플릿을 부모 프리셋 세션의 작업 프롬프트 생성 작업으로 등록하되, 분석 턴의 기존 세션을 resume하지 않고 새 AI 실행기 세션으로 실행되도록 한다.
10. 선택 후보가 있는데 작업 프롬프트 생성 작업이 실행 완료되지 않으면 앱은 내부 로그를 남기고 해당 워크스페이스 큐를 중지한다.
11. 작업 프롬프트 생성 작업이 완료되면 앱은 응답의 `prompts` 배열에서 `candidate_id`, `title`, `prompt`를 추출한다.
12. `prompts` 항목 개수가 선택 후보 개수와 다르면 앱은 내부 로그를 남기고 해당 워크스페이스 큐를 중지한다.
13. 앱은 생성 응답 순서가 아니라 분석 단계의 입력 후보 순서대로 `candidate_id`를 매칭한다.
14. 앱은 부모 프리셋 탭 바로 오른쪽에 `P2-1`, `P2-2` 같은 후보 세션 탭을 후보 순서대로 만든다.
15. 프리셋 등록 시점의 실행 옵션은 부모 프리셋 분석 작업, 작업 프롬프트 생성 작업, 후보 세션 `P<n>-<m>`, 후보 작업과 자동커밋 작업에 동일하게 적용한다.
16. 앱은 각 후보 세션에 생성된 후보 작업 프롬프트를 등록한다.
17. 부모 프리셋 세션에는 완료 후 자동커밋 작업을 등록하지 않는다.
18. 부모 프리셋 세션의 자동커밋이 체크되어 있으면 각 후보 세션의 후보 작업 뒤에 `커밋해 주세요.` 작업을 등록한다.
19. 후보 세션 작업은 등록 직후 같은 워크스페이스의 기존 대기 작업보다 앞선 큐 순서로 재배치되어 부모 프리셋 세션 작업 직후 실행되도록 한다.
20. 앱은 프리셋 턴1 등록/결과 수신/턴2 준비, 턴2 등록/결과 수신/프롬프트 파싱/후보 작업 등록, 턴2 미진행 또는 오류 사유를 내부 로그에 남긴다. 로그에는 가능한 범위에서 job id, workspace tab id, session tab id, 후보 수, 생성 프롬프트 수, 중지 사유를 포함한다.
21. 프리셋 후속 단계의 큐 제어 세대는 작업 등록 시점이 아니라 해당 프리셋 작업이 실제 `실행 중`으로 바뀐 시점의 세대로 판단한다. 따라서 실행 전 큐 중지 후 재시작된 프리셋 작업은 후속 턴을 계속 진행하고, 실행 시작 이후 중지된 작업의 후속 턴은 무시한다.
22. 워크스페이스별 큐 제어 세대는 서로 독립적이어야 한다. 활성 워크스페이스 별칭에 대한 무효화는 다른 워크스페이스에서 실행 중인 프리셋 후속 턴을 무효화하지 않으며, 전체 큐 중지나 종료 같은 명시적 전체 제어만 모든 워크스페이스 후속 작업을 무효화한다.
23. 프리셋 턴1 또는 턴2 완료 후 후속 등록 작업이 대기 중이면 일반 다음 작업 디스패치보다 후속 등록 작업을 먼저 처리해야 한다. 이미 디스패치 요청이 대기 중이어도 프리셋 후속 등록이 끝나기 전에는 기존 대기 작업이 같은 워크스페이스 슬롯을 선점하지 않아야 한다.
24. 같은 워크스페이스에서 프리셋 후속 등록 작업이 여러 개 대기할 수 있으므로, 후속 등록 pending 상태는 워크스페이스별 작업 수 기준으로 유지해야 한다.
25. 실행 중인 프리셋 작업과 무관한 유휴 세션 탭을 닫는 것은 프리셋 턴1에서 턴2로 넘어가는 후속 등록을 무효화하지 않는다. 단, 세션 닫기로 큐가 중지되거나 실행 중인 작업이 취소되면 해당 워크스페이스의 프리셋 후속 등록은 무효화한다.
26. 프리셋 턴1 또는 턴2 완료 뒤 후속 등록을 처리하는 시점에 부모 프리셋 세션 탭이 이미 닫혀 있으면, 앱은 닫힌 세션에 턴2 작업이나 후보 세션을 만들지 않고 후속 등록을 건너뛴다.

### 유스케이스 6: 작업 등록

1. 사용자가 일반 세션 탭에서 프롬프트를 입력한다.
2. 앱은 입력값을 검증하고 작업 객체를 생성한다.
3. 세션 탭의 `자동커밋` 체크박스가 체크되어 있으면 앱은 같은 세션 탭에 `커밋해 주세요.` 프롬프트 작업을 후속 작업으로 추가한다.
4. 앱은 AI 실행기 provider와 실행 옵션을 작업 등록 시점 스냅샷으로 저장한다.
5. 작업을 저장한 뒤 스케줄러가 즉시 실행 가능 여부를 판단한다.

### 유스케이스 7: 작업 실행

1. 앱은 작업에 고정된 AI 실행기 provider/모델/추론 옵션, 현재 설정의 실행기 경로/디렉터리 또는 PATH 명령, 상위 워크스페이스 탭의 path, 세션 탭의 세션 ID 유무를 확인한다.
2. 실행에 필요한 설정이 비어 있거나 워크스페이스 path가 유효하지 않으면 작업을 `설정입력대기` 상태로 전환한다.
3. 세션 탭에 세션 ID가 없으면 선택된 provider의 최초 실행 명령을 준비하고, 세션 ID가 있으면 provider의 세션 재개 명령을 준비한다.
4. 워크스페이스 path는 프로세스 `cwd`로 지정하고, provider가 작업 디렉터리 옵션을 제공하면 같은 path를 옵션에도 반영한다.
5. 실행 가능하면 작업에 고정된 모델과 추론 옵션을 provider별 CLI 인자로 변환해 반영한다.
6. 자동 실행 경로에는 provider별 권한/신뢰/세션 지속 옵션을 계약대로 포함하고, 지속 세션 유지가 필요한 작업에는 ephemeral 또는 임시 세션 옵션을 사용하지 않는다.
7. 프롬프트 전달 방식과 마지막 응답 추출 방식은 선택된 provider 계약을 따른다.
8. 그 다음 백그라운드 작업으로 외부 프로세스를 시작한다. Windows에서는 큐 실행 중 새 콘솔 창이 뜨지 않도록 콘솔 창 생성 억제 옵션을 적용한다.
9. 작업 상태가 `실행 중`으로 바뀌면 세션 이력에 `Prompt:`를 먼저 남긴다.
10. provider별 표준 출력 이벤트 또는 텍스트 로그, 표준 오류, 마지막 응답 아티팩트를 순차적으로 수집한다.
11. 실행 모니터는 `execution_timeout_minutes`와 `inactivity_timeout_minutes`가 양수일 때 각각 전체 실행 시간과 stdout/stderr/provider 이벤트 무활동 시간을 감시한다.
12. 실행 제한을 초과하면 앱은 외부 프로세스 트리에 정상 종료를 요청하고, `termination_grace_seconds`만큼 기다린 뒤 강제 종료를 시도한다.
13. 프로세스 종료 후 stdout/stderr reader가 끝나지 않아도 앱은 무기한 대기하지 않고 짧은 제한 시간 후 내부 경고를 남긴 뒤 실행 결과를 확정한다.
14. provider 계약에 따른 세션 ID가 확인되면 세션 탭에 기록한다.
15. 진행 로그에는 요약 제목과 필요한 짧은 설명만 표시한다.
16. 수집 결과는 메인 스레드에서 UI에 반영한다.
17. 진행 로그는 사용자가 로그 하단을 보고 있거나 아직 로그가 비어 있으면 새 진행 라인이 보이도록 자동으로 하단을 따라간다. 사용자가 이전 로그를 보려고 위로 스크롤한 상태에서는 스크롤 위치를 빼앗지 않는다.
18. 한 세션에 여러 작업이 등록되어 있어도 작업이 실행 중으로 전환되면 진행 로그는 실행 중 작업의 로그를 우선 표시한다.
19. 진행 로그는 파일 로그 저장 옵션과 무관하게 UI 이벤트와 런타임 메모리 버퍼로 항상 남긴다.
20. 파일 로그 저장 옵션이 켜져 있으면 앱은 provider별 stdout 원문, stderr 진단 로그, 프롬프트 원문, 실행 메타데이터를 파일 아티팩트로 저장한다.
21. 파일 로그 저장 옵션이 꺼져 있어도 세션 ID 확인과 완료 판정에 필요한 provider별 stdout 파싱과 마지막 응답 수집은 유지하되, 임시 응답 아티팩트는 실행 결과 확정 뒤 정리하고 `.j3aitaskrunner` 아래에는 실행 로그를 남기지 않는다.

### 유스케이스 8: 작업 완료

1. provider 계약의 완료 신호와 마지막 응답이 확인되면 마지막 응답 텍스트와 실행 결과를 확정한다. Codex provider는 `turn.completed`/`codex.turn.completed`와 마지막 응답 파일을 사용한다.
2. provider 계약의 실패 신호가 확인되면 작업을 `실패`로 저장한다. Codex provider는 `turn.failed`/`codex.turn.failed`를 사용한다.
3. provider별 오류 이벤트가 확인됐더라도 완료 신호와 마지막 응답이 확인되면 작업을 `완료`로 저장하고, 그렇지 않으면 작업을 `실패`로 저장한다. Codex provider는 `error`/`codex.error`에 이 규칙을 적용한다.
4. 전체 실행 제한 또는 무활동 제한 초과로 프로세스를 종료했다면 작업을 `실패`로 저장한다.
   - 전체 실행 제한 초과 사용자 메시지는 `실행 시간이 초과되었습니다.`처럼 일반 실패와 구분해 짧게 표시한다.
   - 무활동 제한 초과 사용자 메시지는 `진행 로그가 없어 실행을 중단했습니다.`처럼 전체 실행 제한 초과와 구분한다.
5. 앱이 사용자 취소로 프로세스를 종료했다면 작업을 `취소됨`으로 저장한다.
6. 현재 런타임 메모리의 세션 이력에서 시작 시 남긴 턴에 `Response:`를 채우고 워크스페이스 전체 작업 목록을 갱신한다.
7. 실패, 취소, timeout을 포함해 실행 중 작업이 최종 상태가 되면 워크스페이스 실행 슬롯을 비우고, 같은 워크스페이스 큐에 다음 대기 작업이 있으면 스케줄러가 후속 실행을 판단한다.
8. 다음 대기 작업이 없고 워크스페이스 작업 목록의 모든 작업이 `완료` 상태이면 큐 상태를 `중지`로 전환한다.
9. timeout으로 실패한 프리셋 분석 또는 작업 프롬프트 생성 결과는 완료 응답으로 취급하지 않으며, 후보 작업 생성 후속 흐름을 시작하지 않는다. 이미 선택된 후보가 있는 프리셋 흐름이면 내부 로그를 남기고 해당 워크스페이스 큐를 중지한다.

### 유스케이스 9: 탭 닫기

1. 사용자가 세션 탭 또는 워크스페이스 탭을 닫는다.
2. 앱은 닫는 범위 안에 실행 중 작업과 대기 중 작업이 있는지 확인한다.
3. 실행 중 작업 또는 대기 중 작업이 있으면 사용자에게 취소/삭제 후 닫을지 확인한다.
4. 사용자가 거부하면 닫기 동작을 중단한다.
5. 사용자가 확인하면 실행 중 작업은 외부 실행기를 명시적으로 종료하고 `취소됨`으로 저장한다.
6. 사용자가 확인하면 `대기` 및 `설정입력대기` 작업은 스케줄러에서 삭제한다.
7. 실행 중 작업이 있던 세션 탭을 닫은 경우 해당 워크스페이스 큐를 `중지` 상태로 바꾸고, 다른 워크스페이스의 `대기` 작업은 유지한다.
8. 워크스페이스 탭을 닫는 경우에는 실행 중 작업 유무와 별개로 해당 워크스페이스 큐를 `중지` 상태로 바꾼다.
9. 앱은 해당 탭의 런타임 `열림/닫힘 상태`를 `닫힘`으로 바꾼다.
10. 특정 워크스페이스 탭 안의 모든 세션 탭이 닫히면 그 워크스페이스에서 다음 새 일반 세션 탭 이름은 다시 `S1`, 다음 새 프리셋 세션 탭 이름은 다시 `P1`부터 시작한다.

## 8. 권장 계층 구조

Python/Tkinter 프로젝트에서는 아래 단방향 의존을 기본으로 한다.

### `ui`

- Tkinter 창, 프레임, 다이얼로그, 이벤트 바인딩
- 워크스페이스 탭 표시
- 워크스페이스 탭 내부의 세션 탭 표시
- 워크스페이스 헤더의 큐 상태/큐 시작-중지 토글/새 세션/새 프리셋/가져오기 제어, provider stdout 기반 진행 로그 영역, 이력 영역, 워크스페이스 우측 작업 목록, 왼쪽 사이드바 하단 설정 버튼과 설정 요약
- 프리셋 세션 탭의 `Language`, `Instruction`, `Work Priority` 입력, prefix 편집 영역, 자동커밋 체크박스 표시
- 프리셋 세션 탭에서는 일반 프롬프트 에디터를 숨기고 프리셋 실행 제어와 분석 프롬프트 prefix 편집 영역을 표시
- 가져오기 모달의 여러 줄 `Text` 편집 영역, 자동커밋 체크박스, 등록 버튼 표시
- 사이드바 워크스페이스 제어는 `등록`/`제거`, `열기`/`닫기`를 2행 2열로 표시한다.
- 실행 중 작업이 있는 워크스페이스 탭은 탭 바에서 녹색으로 식별 가능해야 하며, 모든 작업이 종료되면 녹색 표시를 제거해야 한다.
- 실행 중 작업이 있는 세션 탭도 탭 바에서 녹색으로 식별 가능해야 한다.
- 세션 상단 활동 문구가 `실행 중`이면 현재 실행 중인 작업 ID 오른쪽 괄호에 같은 세션에서 `완료` 상태인 `job-N`의 숫자만 표시해야 하며, 실패/취소/대기/설정 필요 작업은 포함하지 않는다.
- 세션 상단 활동 문구가 `종료`이면 같은 세션에서 `완료` 상태인 `job-N`만 `종료: 완료 job-1, 2, 3` 형식으로 표시해야 한다.
- 세션이 실행 중이 아니고 선택 또는 최근 작업에 실패/취소/타임아웃/설정 같은 사용자 메시지가 있으면 해당 메시지는 별도 보조 메시지 라인이 아니라 같은 `종료` 라인에 표시해야 한다.
- 선택 또는 최근 작업이 `실패`이면 같은 세션에서 `완료` 상태인 `job-N` 숫자를 괄호 안에 표시하고, 실패 메시지는 `종료: 실패 job-N (...) 메시지` 형식으로 같은 줄에 합쳐 표시해야 한다.
- 작업 목록 컨텍스트 메뉴와 프롬프트 원문 보기 모달
- 기본 화면, 설정 요약, 설정 대화상자, 목록, 작업 테이블, 프롬프트/출력 텍스트 영역은 같은 다크 테마 색상 체계를 사용해야 한다.
- 사용자 입력을 `app` 계층 유스케이스로 전달

### `app`

- 워크스페이스 탭/세션 탭 런타임 상태 관리
- 유스케이스 실행
- 입력 검증
- 프로그램 설정 조회와 작업별 실행 옵션 스냅샷 적용
- 스케줄러 조정
- 프리셋 세션 생성, 프리셋 분석 작업 등록, 분석 응답 `candidates` 해석, 후보 세션 생성, 후보 작업 등록
- 부모 프리셋 세션 직후 후보 세션 작업이 실행되도록 큐 우선순위 조정
- 백그라운드 작업과 UI 갱신 연결

### `infra`

- 로컬 파일 기반 저장
- 외부 프로세스 실행
- 작업에 고정된 AI 실행기 provider 실행 명령 조합, 실행 시점 운영 설정 반영, stdout/stderr 파싱, 마지막 응답 확보
- j3aiPromptLoop 호환 `prompt/<Language>/<instruction>.md`와 `<instruction>_work.md` 프롬프트 템플릿 파일 읽기
- 파일시스템 접근
- 플랫폼별 알림음, 경로 탐색, OS 연동

### `domain`

- 워크스페이스 탭과 워크스페이스의 1대1 규칙
- 세션 탭 소속 규칙
- 일반/프리셋/후보 세션 탭 이름 규칙
- 세션 ID 유보/확정 규칙
- 프리셋 후보 세션 생성 및 우선순위 규칙
- 다음 작업 선택 규칙
- 세션/완료 정렬 규칙
- 입력 규칙처럼 I/O 없이 검증 가능한 로직

## 9. Tkinter 구현 제약

이 항목은 기술 제약이지만, 실제 구현 품질에 직접 영향을 주므로 도메인 문서에 포함한다.

1. Tkinter 위젯 조작은 메인 스레드에서만 수행한다.
2. 외부 프로세스 실행, 로그 수집, 파일 저장은 UI 이벤트 핸들러에서 직접 블로킹하지 않는다.
3. 백그라운드 작업 결과는 `queue.Queue`와 `after(...)` 루프로 메인 스레드에 전달한다.
4. 워크스페이스 탭과 세션 탭이 많아져도 UI 전체를 매번 다시 그리지 말고, 필요한 영역만 부분 갱신한다.
5. 팝업과 설정 대화상자는 모달로 띄우고, 표시 전에 크기와 위치를 계산해 메인 창 기준 중앙에 배치한다. 메인 창이 가상 데스크톱의 음수 좌표 모니터에 있어도 좌표를 0으로 보정하지 않고 메인 창 기준 중앙을 유지한다.
6. 실시간 로그처럼 계속 증가하는 텍스트 영역은 사용자가 하단을 보고 있을 때 새 내용이 화면에 보이도록 하단 추적을 유지한다.
7. 창 종료 시 실행 중 작업, 폴링 루프, 예약된 `after` 콜백을 안전하게 정리한다.
8. Windows DPI awareness 설정은 Tk 루트 생성 전에 한 번 시도하고, Windows 전용 API 접근은 `infra` 계층의 지연 import 헬퍼에 둔다.
9. Windows DPI awareness는 관리 도구형 UI 안정성을 위해 `SYSTEM_AWARE` context를 먼저 시도하고, 실패 시 per-monitor context, shcore system/per-monitor, legacy `SetProcessDPIAware()` 순서로 fallback한다.
10. Tk 루트 생성 직후 현재 DPI를 읽어 `tk scaling`을 적용하고, 기본 Tk UI 폰트는 Windows에서 `Malgun Gothic` 계열로 맞춘다. 출력 로그처럼 기존 고정폭 폰트가 필요한 영역은 `Consolas` 설정을 유지한다.
11. 프레임 padding, 패널 초기 폭, 프롬프트/출력 패널 높이, Treeview rowheight, classic widget border/highlight 등 내부 픽셀 기반 값만 `UiScale`로 보정한다. 메인 창의 초기 Tk client geometry는 실행 시 1100 x 800을 유지하고, 최소 geometry는 800 x 600을 유지한다. 왼쪽 사이드바 초기 폭은 180 px이고, 접힌 상태의 버튼 바 폭은 36 px이며, 워크스페이스 탭 내부 오른쪽 작업 목록 영역 초기 폭은 180 px이고, 세션 영역은 남은 폭을 사용한다. `Entry`, `Combobox`, `Button`, `Text`의 문자 수 기반 `width`/`height` 값은 스케일하지 않는다.
12. root `<Configure>` 기반 DPI 동기화는 전용 컨트롤러가 담당하며, 연속 이벤트를 debounce하고 같은 DPI이면 아무 작업도 하지 않는다. 종료 시 예약된 DPI 동기화 `after` 콜백은 반드시 취소한다.
13. DPI 변경 callback에서는 필요한 widget option과 스타일만 다시 적용하고, 창 `geometry()`를 되먹임하지 않는다. Win32 `WM_DPICHANGED` subclassing, native size/move capture 해제, 동기 paint flush 같은 직접 Win32 message-loop 개입은 Tk 앱의 기본 구현으로 사용하지 않는다.

## 10. 릴리즈 빌드 제약

릴리즈 빌드는 Windows와 Linux에서 같은 Python 스크립트로 실행할 수 있어야 한다.

1. 릴리즈 빌드는 PyInstaller `onedir` 방식으로 생성하며, `onefile` 방식은 사용하지 않는다.
2. 산출물 루트는 `dist/<플랫폼이름>/j3AITaskRunner/` 형식을 사용한다.
3. 실행 파일은 산출물 루트에 두고, 라이브러리와 번들 리소스는 `dist/<플랫폼이름>/j3AITaskRunner/lib/` 아래에 둔다.
4. 번들 리소스에는 앱 아이콘 파일인 `assets/app_icon.ico`, `assets/app_icon.png`, 앱 GPL-3.0 라이선스 파일인 `LICENSE`, 제3자 라이선스 고지 문서인 `THIRD_PARTY_NOTICES.txt`, About 표시용 `about.txt`, 앱 아이콘의 `LICENSES/APACHE-2.0.txt`, 빌드 인터프리터의 Python `LICENSE.txt` 사본, PyInstaller 라이선스 사본, `tkinterdnd2` 라이선스 사본을 포함한다. 필요한 라이선스 파일을 찾을 수 없으면 배포 빌드는 실패해야 한다.
5. Windows 릴리즈 빌드는 `app/version.py`의 앱 버전으로 실행 파일 `FileVersion`과 `ProductVersion` 리소스를 생성한다.
6. `build_release.py`는 현재 플랫폼 이름 기준의 빌드 전용 venv인 `.build-venv/<플랫폼이름>/`를 준비하고, PyInstaller 6 이상과 `tkinterdnd2`를 venv에 설치한 뒤 같은 스크립트를 venv Python으로 재실행해 빌드한다. POSIX venv의 Python 실행 파일이 시스템 Python을 가리키는 심볼릭 링크일 수 있으므로, venv 실행 여부는 실행 파일의 실제 타깃이 아니라 `sys.prefix`가 빌드 venv인지로 판단한다.
7. Windows와 Linux의 venv는 실행 파일 위치와 설치되는 바이너리 wheel이 다르므로 서로 공유하지 않고 플랫폼별 venv로 분리한다.
8. 빌드 스크립트가 자동 설치하는 대상은 Python venv와 pip 패키지로 제한한다. OS 패키지 수준의 Python/Tk 구성 요소가 없으면 사용자가 해당 OS에서 별도로 설치해야 한다.
9. 빌드 완료 후에는 사용자가 산출물을 바로 확인할 수 있도록 산출물 루트를 OS 파일 탐색기로 연다.
10. 소스 ZIP에는 현재 프로젝트 소스와 문서만 포함하고, 루트의 생성물성 `lib/`, `build/`, `dist/`, 가상환경, 캐시, 로그, 데이터 디렉터리는 포함하지 않는다.
11. 공개 배포 전에 PyInstaller 산출물에 포함된 Python/Tcl/Tk/native library, OpenSSL, SQLite, zlib, expat, libffi, bzip2, XZ/liblzma, libmpdec, mimalloc, Zstandard, `tkinterdnd2`, `tkinterdnd2-universal` 유래 파일, tkDnD 바이너리/스크립트 버전을 확인하고, `THIRD_PARTY_NOTICES.txt`의 고지 대상과 맞지 않으면 문서를 먼저 갱신한다.
12. GPL-3.0 공개 바이너리 배포는 실행 파일만 제공하지 않고, 해당 릴리스 버전에 대응하는 전체 소스 코드 또는 GPL-3.0이 허용하는 소스 제공 방식을 함께 준비한다.
13. PyInstaller가 수집한 패키지 `LICENSE`, `NOTICE`, `.dist-info` 메타데이터는 기본적으로 배포물에서 제거하지 않는다. 제거가 필요하면 같은 고지 내용을 `THIRD_PARTY_NOTICES.txt`나 별도 라이선스 파일로 보존한 뒤 배포한다.
14. 앱 아이콘은 Google Material Symbols `checklist` 글리프(codepoint `e6b1`)에서 파생된 Apache-2.0 고지 대상으로 기록하고 `LICENSES/APACHE-2.0.txt`를 배포물에 포함한다. 아이콘 파일이 교체되면 새 아이콘의 패밀리/글리프, 라이선스, 저작권 문구, 필요한 라이선스 파일을 다시 확인한다. 프롬프트 자산이 프로젝트 자체 제작물이 아니라면 공개 배포 전에 해당 출처와 라이선스를 `THIRD_PARTY_NOTICES.txt`에 추가한다.

## 11. 상태 모델

구현 방식은 자유지만, 상태는 `지속 데이터`와 `런타임 상태`를 구분해야 한다.

### 지속 데이터: 앱 설정

- AI 실행기 provider(`agent_provider`, 기본값 `codex`, 지원값 `codex`/`claude_code`/`kilo_code`/`opencode`/`pi`)
- 기본 모델(`default_model`, 빈 값이면 provider 기본 모델)
- 기본 추론 레벨(`default_reasoning_effort`, 빈 값이면 provider 또는 모델 기본 추론 옵션)
- 현재 선택된 AI 실행기의 외부 실행기 경로/디렉터리 또는 PATH 명령(`executable_path`, 호환 필드)
- AI 실행기별 외부 실행기 경로/디렉터리 또는 PATH 명령(`executable_paths`, provider id를 키로 쓰는 맵)
- 출력 글꼴 크기
- 전체 실행 제한 시간(`execution_timeout_minutes`, 기본값 `120`)
- 출력 무활동 제한 시간(`inactivity_timeout_minutes`, 기본값 `30`)
- 정상 종료 요청 후 강제 종료까지의 유예 시간(`termination_grace_seconds`, 기본값 `5`)
- 로그 파일 저장 사용 여부(`file_logging_enabled`)
- UI 언어(`ui_language`, 기본값 `en`, 지원값 `ko`/`en`)
- 전체 실행 제한과 출력 무활동 제한은 도메인 해석에서 `0`이면 비활성화한다. 종료 유예 시간은 `0`이면 즉시 강제 종료를 뜻한다. UI와 저장소 입력 검증은 빈 값, 음수 또는 비정수 값을 허용하지 않고 기본값으로 되돌리거나 저장 전 차단한다.
- 설정 변경은 새로 시작하는 실행 요청부터 적용한다. 이미 실행 중인 외부 프로세스는 시작 시점에 받은 설정 스냅샷을 계속 사용한다.
- 설정의 기본 AI 실행기/model/추론 레벨은 워크스페이스에서 마지막으로 고른 실행 옵션이 없을 때 새 일반 세션, 새 프리셋 세션, 가져오기 세션, 작업 등록 fallback의 초기 실행 옵션으로 사용한다.
- 앱은 스크립트 실행 시 스크립트가 있는 폴더, 실행 파일 배포본에서는 실행 파일이 있는 폴더의 `j3AITaskRunner.json`에 설정과 저장된 워크스페이스 목록을 함께 저장한다.
- 설정 대화상자에서 AI 실행기를 전환해도 각 provider의 실행기 경로 입력값은 서로 덮어쓰지 않고 provider별로 보존한다.
- 저장소는 새 설정 파일에 `executable_paths`를 저장한다. 기존 `executable_path`만 있는 설정 파일은 현재 `agent_provider`의 실행기 경로로 마이그레이션해 읽는다.
- 과거 설정 파일에 `agent_provider`가 없으면 `codex`로 해석한다. 알 수 없는 provider 값은 앱 시작/import를 깨뜨리지 않고 `codex`로 정규화한다.

### 지속 데이터: 저장된 워크스페이스 목록

- 워크스페이스 path
- 표시 이름 또는 폴더명
- 추가 시각
- 마지막 선택 시각

### 런타임 상태: 워크스페이스별 큐 상태

- 워크스페이스 탭 ID
- 해당 워크스페이스 큐 상태(`시작` 또는 `중지`)
- 해당 워크스페이스에서 현재 실행 중 작업 ID(없을 수 있음)
- 마지막 정지 사유(선택: 사용자 정지, 실행 중 탭 닫기 등)

### 런타임 상태: 워크스페이스별 실행 슬롯

- 각 워크스페이스 큐의 현재 실행 중 작업 ID(없을 수 있음)
- 한 워크스페이스에서 동시에 실행 가능한 작업 수는 항상 `1`
- 여러 워크스페이스의 실행 슬롯은 서로 독립적이다.

### 런타임 상태: 세션 탭 번호 상태

- 워크스페이스 탭별 다음 일반/프리셋 세션 탭 기본 이름 번호
- 일반 세션 탭 `S<n>`과 프리셋 세션 탭 `P<n>`은 같은 번호 카운터를 공유한다.
- 프리셋 후보 세션 탭 `P<n>-<m>`은 부모 프리셋 세션의 후보 순번을 사용하고 공유 번호 카운터를 증가시키지 않는다.
- 특정 워크스페이스 탭 안의 모든 세션 탭이 닫히면 해당 워크스페이스의 세션 탭 번호를 `1`로 초기화

### 런타임 상태: 워크스페이스 탭 상태

- 워크스페이스 탭 ID
- 워크스페이스 path
- 표시 이름(워크스페이스 path의 마지막 폴더명)
- 열림/닫힘 상태
- 정렬 순서
- 현재 활성 세션 탭 ID
- 생성/수정 시각

### 런타임 상태: 세션 탭 상태

- 세션 탭 ID
- 부모 워크스페이스 탭 ID
- 세션 탭 종류(`일반`, `프리셋`, `프리셋후보`)
- 표시 이름
- 세션 ID(미확정 가능)
- 자동커밋 체크 상태
- 프리셋 입력값(프리셋 세션인 경우: `Language`, `Instruction`, `Work Priority`)
- 부모 프리셋 세션 탭 ID(프리셋 후보 세션인 경우)
- 후보 순번(프리셋 후보 세션인 경우)
- 열림/닫힘 상태
- 정렬 순서
- 생성/수정 시각

### 런타임 상태: 작업 상태

- 작업 ID
- 부모 워크스페이스 탭 ID
- 부모 세션 탭 ID
- 작업 유형(일반 프롬프트, 프리셋 분석, 프리셋 후보, 자동커밋 등)
- 프롬프트
- 상태
- 설정입력대기 사유(선택)
- 사용자 메시지(선택)
- 큐 순서
- 큐 우선순위 메타데이터(선택: 프리셋 후보 세션의 부모 직후 실행 우선순위 등)
- 프로세스 메타데이터(pid, 종료 코드, launch command 등)
- 마지막으로 적용된 실행 메타데이터(디버깅용, 선택: `agent_provider`, 모델, 추론 옵션, `agent_version`; 기존 `codex_cli_version`은 호환 필드로 유지)
- 생성/시작/완료 시각
- 앱 종료 시 복원하지 않음

### 런타임 상태: 실행 아티팩트

- 작업 ID
- 파일 로그 저장 옵션이 켜져 있으면 launch 메타데이터 파일, 프롬프트 원문 파일, provider stdout 원문 파일, 표준 오류 로그 파일을 저장한다.
- 마지막 응답 아티팩트는 작업 결과 확정을 위해 파일 로그 저장 옵션과 무관하게 확보한다. 파일 로그 저장 옵션이 꺼져 있으면 임시 위치를 사용하고 실행 결과 확정 후 정리한다.
- 도메인 상태 복원 대상은 아니며, 디버깅과 재현을 위한 구현 아티팩트다.

### 런타임 상태: 세션 턴 이력

- 워크스페이스 path
- 세션 탭 ID
- 세션 ID(최초 실행 중에는 미확정 가능)
- 작업 ID
- 프롬프트 텍스트
- 응답 텍스트(완료 전에는 비어 있을 수 있음)
- 시작/완료 시각
- 마지막 활동 시각
- 앱 종료 시 복원하지 않음

## 12. 오류 처리와 사용자 메시지

- 내부 로그에는 예외 원인, 외부 프로세스 종료 코드, 파싱 실패 정보, 저장된 경우 표준 출력 원문 경로와 표준 오류 로그 경로, 마지막 응답 아티팩트 경로, AI 실행기 provider와 버전을 남긴다.
- 프리셋 분석 응답 또는 작업 프롬프트 생성 응답이 JSON/데이터 계약을 만족하지 못하면 UI에는 짧은 사용자 메시지만 보여주고, 내부 로그나 세션 출력 아티팩트에는 실패한 응답 원문을 추적할 수 있어야 한다.
- provider stdout 파싱 결과와 마지막 응답 아티팩트 내용이 어긋나면 내부 경고를 남긴다.
- 표준 오류는 사용자 응답 소스가 아니라 진단 로그로 취급한다.
- 사용자 메시지는 "무엇이 실패했고, 사용자가 무엇을 할 수 있는지"를 중심으로 짧게 보여준다.
- 복구 가능한 문제는 작업을 즉시 버리지 않고, `설정입력대기` 또는 재시도 가능한 상태를 우선 유지한다.

### UI 문구 기준

- 화면 문구는 짧은 작업형 표현을 우선한다. 예: `작업 등록`보다 `등록`, `실시간 진행 로그`보다 `진행 로그`.
- 내부 상태명 `설정입력대기`는 UI에서 `설정 필요`로 표시한다.
- 로그 파일 저장 옵션은 단순 `로그 사용`이 아니라 `파일 로그 저장`으로 표시해 실시간 로그와 구분한다.
- 프리셋 입력 라벨은 화면에서 `언어`, `지시문`, `우선순위`로 표시하고, 내부 값과 프롬프트 자산 구조는 기존 `Language`, `Instruction`, `Work Priority` 용어를 유지한다.
- 오류 메시지는 UI에서 내부 원인 전체를 설명하지 않고, 사용자가 확인할 대상만 짧게 안내한다.
- 하단 상태바와 설정 대화상자의 실행기 버전 조회 결과처럼 app/infra 계층에서 전달된 사용자 메시지도 현재 UI 언어에 맞춰 표시한다.
- 작업이 `대기` 또는 `실행 중`으로 전환될 때는 세션 상단 보조 메시지 라인에 상태 반복 문구를 표시하지 않는다.

## 13. 현재 코드 구조

현재 구현은 아래 구조를 기준으로 유지한다.

```text
main.py
ui/
  main_window.py
  dialogs.py
  theme.py
  windowing.py
  dpi.py
  i18n.py
app/
  controller.py
  runtime.py
  workspace_manager.py
  session_manager.py
  scheduler.py
  use_cases.py
  execution_worker.py
infra/
  process_runner.py
  agent_contract.py
  repository.py
  *_adapter.py
  *_jsonl.py
domain/
  models.py
  policies.py
  preset.py
docs/
  domain.md
  development-log.md
```

핵심 원칙은 폴더 수보다 책임 분리다. UI는 화면과 입력 전달만 담당하고, 워크스페이스/세션 상태 관리와 실행 흐름은 `app`, 외부 I/O는 `infra`, I/O 없이 검증 가능한 규칙은 `domain`에 둔다.

## 14. Known Risk

1. Claude Code, Kilo Code, OpenCode, Pi Coding Agent provider는 현재 프롬프트를 subprocess argv 위치 인자로 전달한다. shell quoting은 피하지만 Windows command line 길이 제한에는 걸릴 수 있으므로, 긴 프롬프트 실패는 launch metadata의 `prompt_delivery`와 adapter warning 로그를 먼저 확인한다.
2. OpenCode/Kilo Code stdout schema와 Pi JSON Event Stream schema는 로컬 설치 버전별로 바뀔 수 있다. 기본 테스트는 fake executable/Popen 계약으로 고정하고, 실제 CLI 계약은 `J3AITASKRUNNER_RUN_REAL_AGENT_SMOKE=1` opt-in smoke와 로컬 `--help` 또는 JSON mode 결과로 재검증한다.
3. Codex CLI 계약은 alpha 계열 변경 가능성이 있다. `codex exec --json`, `codex exec resume --json`, `--skip-git-repo-check`, `-o <last_message_file>` 동작이 바뀌면 `CodexCliAdapter`와 `tests/test_process_runner.py`의 Codex command/result tests를 함께 갱신한다.
4. 파일 로그 저장이 꺼져도 마지막 응답 확보를 위한 임시 아티팩트가 필요하다. 실패 원인이 임시 디렉터리 권한/용량이면 사용자 메시지는 짧게 표시되고, 내부 로그의 artifact storage failure를 확인한다.
5. `j3AITaskRunner.json`의 과거 키는 저장소에서 정규화한다. `progress_logging_enabled`는 `file_logging_enabled`로 읽고, 누락/알 수 없는 `agent_provider`는 `codex`로 해석한다. 기존 단일 `executable_path`는 현재 provider의 `executable_paths` 항목으로 읽는다. 새 설정 키를 추가하면 누락값 기본화와 저장 round-trip 테스트를 먼저 추가한다.
