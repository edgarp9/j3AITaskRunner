# j3AITaskRunner 문서 안내

AI 에이전트는 필요한 문서만 읽는다. 필수 작업 규칙과 제외 경로는 루트 [`AGENTS.md`](../AGENTS.md)가 소유한다.

## 문서 목록

| 문서 | 읽는 경우 |
| --- | --- |
| [`features.md`](features.md) | 기능 설명, 도메인 용어, 큐/세션/프리셋/가져오기, provider 실행 계약을 확인할 때 |
| [`architecture.md`](architecture.md) | 계층 책임, 코드 구조, facade, 수정 시작점을 확인할 때 |
| [`build-and-test.md`](build-and-test.md) | 실행 명령, 빠른/전체 검증, UI smoke, 테스트 선택 기준을 확인할 때 |
| [`ui.md`](ui.md) | Tkinter 화면, 상태 표시, 다이얼로그, 프리셋 UI, UI 문구, 메인 스레드 제약을 확인할 때 |
| [`platform-notes.md`](platform-notes.md) | Windows/Linux, Tkinter DPI, 외부 프로세스, provider CLI 변동 위험을 확인할 때 |
| [`release.md`](release.md) | PyInstaller 릴리즈 빌드, 배포 전 검증, 산출물 구조를 확인할 때 |
| [`license.md`](license.md) | 라이선스 파일, About/Licenses 표시, GPL-3.0 공개 배포 준수를 확인할 때 |
| [`decisions.md`](decisions.md) | 현재도 유효한 기술 결정과 운영 규칙의 배경을 확인할 때 |

## 작업별 읽기 순서

| 작업 유형 | 먼저 읽기 | 필요시 추가 |
| --- | --- | --- |
| 문서만 수정, 문서 구조 변경 | 변경 대상 문서, [`build-and-test.md`](build-and-test.md) | 관련 주제 문서 |
| 기능/도메인/큐/프리셋/가져오기 | [`features.md`](features.md) | [`architecture.md`](architecture.md), [`ui.md`](ui.md), [`build-and-test.md`](build-and-test.md) |
| Provider/CLI 실행 계약 | [`features.md`](features.md), [`platform-notes.md`](platform-notes.md) | [`architecture.md`](architecture.md), [`build-and-test.md`](build-and-test.md) |
| 계층/모듈/수정 시작점 | [`architecture.md`](architecture.md) | [`decisions.md`](decisions.md), 변경 기능의 주제 문서 |
| UI/Tkinter/문구/다이얼로그/DPI | [`ui.md`](ui.md), [`platform-notes.md`](platform-notes.md) | [`features.md`](features.md), [`build-and-test.md`](build-and-test.md) |
| 테스트/검증 | [`build-and-test.md`](build-and-test.md) | 변경 영역의 주제 문서 |
| 릴리즈 빌드/배포 | [`release.md`](release.md), [`build-and-test.md`](build-and-test.md) | [`license.md`](license.md), [`platform-notes.md`](platform-notes.md) |
| 라이선스/고지/배포 준수 | [`license.md`](license.md), [`release.md`](release.md) | [`platform-notes.md`](platform-notes.md) |

## 갱신 기준

- 문서에는 현재 계약, 실행 방법, 검증 기준, 주의사항만 남긴다.
- 과거 작업 히스토리, 완료된 변경 내역, 오래된 논의, 임시 메모, 개인 메모, 중복 TODO는 남기지 않는다.
- 같은 설명은 한 문서에만 상세히 둔다. 다른 문서에서는 링크나 짧은 언급만 둔다.
- 현재도 유효한 기술 결정은 [`decisions.md`](decisions.md)나 해당 주제 문서에 남긴다.
- 문서 이름이 바뀌면 루트 [`README.md`](../README.md), [`AGENTS.md`](../AGENTS.md), 이 라우터의 링크를 함께 갱신한다.
