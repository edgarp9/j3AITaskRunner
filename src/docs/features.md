# 기능 설명 문서

`j3AITaskRunner`의 현재 기능, 도메인 용어, 작업 흐름, provider 실행 계약을 확인할 때 읽는다.

## 핵심 기능

- 여러 워크스페이스 폴더를 탭으로 열고, 각 워크스페이스 안에 여러 세션 탭을 둔다.
- 세션 탭의 프롬프트 작업을 `per_workspace` 또는 `shared` 큐 방식으로 실행한다.
- 일반 세션은 현재 프롬프트를 일반 큐 슬롯과 별개로 `바로실행`할 수 있다.
- 프리셋 세션은 `prompt/<Language>/<instruction>.md`와 `<instruction>_work.md` 쌍을 사용해 분석 후보와 후보 작업을 만든다.
- Markdown ` ```text ` 코드 블록 여러 개를 단일 세션 또는 Step별 세션 작업으로 가져온다.
- 세션별 종료 훅을 설정해 해당 세션의 작업이 모두 최종 상태가 된 뒤 외부 실행파일을 1회 실행한다.
- provider stdout 진행 로그는 파일 로그 저장 옵션과 무관하게 UI에 항상 표시한다.
- 저장되는 것은 앱 설정과 저장된 워크스페이스 목록이다. 작업, 큐, 세션 탭, 세션 턴 이력, 세션 종료 훅 설정은 앱 재시작 후 복원하지 않는다.

## 핵심 용어

| 용어 | 의미 |
| --- | --- |
| 워크스페이스 | 사용자가 작업 대상으로 선택한 폴더 path. 외부 실행기의 `cwd`가 된다. |
| 워크스페이스 탭 | 하나의 워크스페이스 path를 여는 최상위 탭. 같은 path는 중복으로 열지 않는다. |
| 세션 탭 | 워크스페이스 안의 대화/작업 흐름 단위. 세션 ID, 이름, 자동커밋, 실행 옵션을 가진다. |
| 일반 세션 | 사용자가 직접 입력한 프롬프트를 등록하는 `S<n>` 탭. |
| 프리셋 세션 | `Language`, `Instruction`, `Work Priority`, prefix로 후보 작업을 만드는 부모 `P<n>` 탭. |
| 프리셋 후보 세션 | 프리셋 분석 후보별 작업을 실행하는 `P<n>-<m>` 탭. |
| 작업(Job) | 특정 세션 탭에 등록된 단일 프롬프트 실행 요청. 등록 시점 provider/model/추론 옵션 스냅샷을 가진다. |
| provider | Codex CLI, Claude Code, Kilo Code, OpenCode, Pi Coding Agent 같은 외부 Agent CLI. |

## 도메인 규칙

1. 워크스페이스 탭과 워크스페이스 path는 1대1이다.
2. 세션 탭은 정확히 하나의 워크스페이스 탭에 속한다.
3. 작업은 정확히 하나의 세션 탭에 속하고 실행 시 상위 워크스페이스 path를 사용한다.
4. 세션 ID는 provider 실행으로 확인되기 전까지 비어 있을 수 있으며, 확정 후에는 세션 탭과 1대1로 매핑된다.
5. 작업 상태는 `대기`, `설정입력대기`, `실행 중`, `완료`, `실패`, `취소됨` 중 하나다.
6. 작업 실행 전 필요한 설정이 비어 있거나 워크스페이스 path가 유효하지 않으면 `설정입력대기`로 둔다.
7. 실행 중 작업은 `완료`, `실패`, `취소됨` 중 하나로만 종료한다.
8. 저장된 워크스페이스 제거는 저장 목록에서만 제거하며 실제 폴더, 열린 탭, 세션, 작업은 삭제하지 않는다.

## 큐와 실행

- `per_workspace`: 워크스페이스 탭마다 큐 시작/중지 상태와 실행 슬롯 1개를 가진다. 서로 다른 워크스페이스 큐는 동시에 실행될 수 있다.
- `shared`: 열린 모든 워크스페이스가 전역 공유큐 상태와 실행 슬롯 1개를 공유한다. 작업 선택은 전역 `queue_order` FIFO다.
- `설정입력대기` 작업은 실행 후보에서 제외한다.
- 큐가 중지되면 해당 큐 슬롯의 실행 중 프로세스를 종료하고 새 작업 선택을 중단한다.
- 큐 범위의 작업이 모두 최종 상태가 되면 큐는 자동 중지된다.
- 큐 시작 상태이거나 실행 중 작업이 있으면 OS 유휴 절전을 방지하고, 모두 끝나면 해제한다.
- 작업큐 방식은 실행 중 작업이 없을 때만 변경할 수 있으며, 변경하면 현재 런타임 작업 목록 전체를 삭제하고 큐 상태를 중지한다.
- 일반 세션 `바로실행`은 일반 큐/공유큐 슬롯을 점유하지 않는다. 같은 세션에 `대기`, `설정입력대기`, `실행 중` 작업이 있으면 거부한다.
- 세션 종료 훅은 해당 세션에 `대기`, `설정입력대기`, `실행 중` 작업이 없고 모든 남은 작업이 `완료`, `실패`, `취소됨`이 된 순간 실행된다.
- 자동커밋 작업이 등록된 세션은 자동커밋까지 최종 상태가 된 뒤 종료 훅을 실행한다.
- 종료 훅은 사용 여부가 켜져 있고 실행파일 경로가 있을 때만 실행한다. 이미 종료된 세션에 나중에 훅을 설정해도 즉시 실행하지 않고, 새 작업으로 다시 활성화된 뒤 종료될 때만 실행한다.

## 프리셋 흐름

1. 프리셋 자산은 `prompt/<Language>/<instruction>.md`와 `<instruction>_work.md` 쌍이다.
2. `Language`와 `instruction`은 단일 경로 세그먼트여야 하며 절대경로, 상위경로, 슬래시, 역슬래시, Windows 금지 문자, 제어 문자, 예약 장치명, 앞뒤 공백, 끝의 점/공백을 포함하면 거부한다.
3. 분석 응답은 최상위 `candidates` 배열을 가진 JSON 객체다. 각 후보는 `id`, `title`, `problem`, `evidence`, `priority`, `risk`, `impact`를 가진다.
4. 작업 프롬프트 템플릿에는 `{{candidates_payload}}` 자리표시자가 있어야 한다.
5. 작업 프롬프트 생성 응답은 최상위 `prompts` 배열을 가진 JSON 객체다. 각 항목은 `candidate_id`, `title`, `prompt`를 포함한다.
6. `prompts` 수는 선택 후보 수와 같아야 하며, 후보 매칭은 생성 응답 순서가 아니라 분석 단계 입력 후보 순서로 한다.
7. `Work Priority`는 `high`, `medium`, `low`, `manual` 중 하나이며 기본값은 `medium`이다.
8. `manual`은 `per_workspace`에서만 허용한다. 전체 후보를 부모 프리셋 세션의 `후보` 탭에 표시하고 사용자가 `Continue`를 누를 때까지 후속 턴을 만들지 않는다.
9. prefix는 분석 프롬프트 상단에만 붙이며 실제 prompt 파일을 수정하지 않는다.
10. 부모 프리셋 세션에는 분석 완료 후 자동커밋 작업을 등록하지 않는다. 자동커밋은 후보 세션의 후보 작업 뒤에만 적용한다.
11. `per_workspace`에서 후보 작업은 부모 프리셋 작업 직후 실행될 수 있도록 우선순위를 가진다. `shared`에서는 FIFO를 유지한다.
12. 프리셋 후속 등록 처리 시 부모 프리셋 세션이 닫혀 있으면 턴2 작업이나 후보 세션을 만들지 않는다.

## 가져오기

- 가져오기는 Markdown ` ```text ` 코드 블록만 추출한다. 코드 블록 하나가 Step 하나다.
- 코드 블록 바깥 텍스트는 무시한다.
- 닫히지 않은 코드 블록, 유효한 코드 블록 없음, 빈 내용은 오류로 표시하고 세션/작업을 만들지 않는다.
- `단일 세션`은 새 일반 세션 하나에 모든 Step 작업을 입력 순서대로 등록한다.
- `Step별 세션`은 Step마다 새 일반 세션을 만들고 해당 Step 작업을 등록한다.
- 자동커밋이 켜져 있으면 각 Step 작업 바로 뒤에 `커밋해 주세요.` 작업을 등록한다.

## Provider 실행 계약

공통 계약:

- 자동 실행은 provider의 비대화형/스크립트용 명령을 사용한다. 대화형 TUI는 큐 실행 경로로 사용하지 않는다.
- 세션 ID가 없으면 최초 실행 명령, 있으면 재개 명령을 사용한다.
- 외부 프로세스 `cwd`는 항상 워크스페이스 path다.
- stdout은 provider별 구조화 이벤트 또는 텍스트 로그로 파싱하고 원문 라인은 UI와 파일 로그에 축약 없이 남긴다.
- stderr는 사용자 응답 소스가 아니라 진단 로그로 취급한다.
- 파일 로그 저장이 꺼져도 세션 ID 확인, 완료 판정, 마지막 응답 수집은 유지한다.
- timeout, 사용자 취소, 프로세스 트리 종료, 마지막 응답 판정은 provider가 바뀌어도 유지되는 앱 공통 계약이다.

| provider | 자동 실행 계약 | 세션/완료 판정 |
| --- | --- | --- |
| `codex` | `codex exec --json --skip-git-repo-check -C <workspace> ... -o <last_message_file> -`, 재개는 `codex exec resume --json --skip-git-repo-check <session_id> ... -o <last_message_file> -` | 세션 ID는 `thread.started.thread_id` 또는 `codex.thread.started.thread_id`. 성공은 정상 종료, `turn.completed`/`codex.turn.completed`, 마지막 응답 파일 확인을 모두 포함한다. |
| `claude_code` | `claude -p <prompt> --output-format stream-json --verbose --include-partial-messages [--resume <id>] [--model <model>]` | 세션 ID는 stream-json `session_id`. 성공은 `result.subtype=success`와 마지막 응답 텍스트 확인을 포함한다. |
| `kilo_code` | `kilo run --format json --dir <workspace> [--session <id>] [--model <value>] [--variant <value>] <prompt>` | stdout JSON/OpenCode 계열 이벤트에서 session/assistant text/완료성 `step_finish`를 변환한다. |
| `opencode` | `opencode run --format json --dir <workspace> [--session <id>] [--model <value>] [--variant <value>] <prompt>` | Kilo Code와 같은 OpenCode 계열 이벤트 변환을 사용한다. |
| `pi` | `pi --mode json [--session <id>] [--model <value>] [--thinking <level>] <prompt>` | 첫 `session` 이벤트 `id`를 세션 ID로 저장한다. `turn_end` 또는 `agent_end`와 assistant message를 완료/마지막 응답으로 본다. |

권한 우회 플래그(`--dangerously-skip-permissions`, `--auto`, bypass 계열)는 기본 명령에 포함하지 않는다. 명시 UI/문서 계약 없이 추가하지 않는다.
