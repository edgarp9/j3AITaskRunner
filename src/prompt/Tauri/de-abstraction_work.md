다음 입력 후보 각각에 대해 OpenAI Codex가 별도 래퍼 없이 그대로 실행할 수 있는 완결형 최종 작업 프롬프트를 작성하라.

규칙:
- 각 프롬프트는 Tauri De-abstraction 작업만 지시한다.
- 해당 후보의 id, title, problem, evidence, priority, risk, impact를 프롬프트에 자연스럽게 반영한다.
- 생성하는 각 prompt 문자열은 반드시 `/goal `로 시작하고, `/goal` 뒤에 한 칸을 둔 다음 최종 작업 프롬프트 본문을 작성한다.
- Codex 작업 안전 규칙을 프롬프트 본문에 직접 포함한다.
- 각 프롬프트는 해당 후보 작업이 시작되기 직전 새 Codex 세션에서 단독 실행된다는 전제를 직접 포함한다.
- 각 프롬프트는 이전 후보, 이전 세션, 병렬 작업 문맥을 전혀 전제하지 않는 완결형 지시문이어야 한다고 명시한다.
- 제외 경로/패턴은 dist/, .git/, .my/, build/, log/, __pycache__/, *.pyd, *.pyc, *.pyo, *$py.class, .venv/, venv/, env/, ENV/, site-packages/, .ruff_cache/, .pytest_cache/, .mypy_cache/, .pyre/, .hypothesis/, .tox/, .nox/, .eggs/, *.egg-info/, pip-wheel-metadata/, .coverage, .coverage.*, coverage.xml, htmlcov/, *.log, *.tmp, *.bak, .vscode/, .idea/, .DS_Store, Thumbs.db, Desktop.ini, *~, *.swp, *.swo, .nfs*, .fuse_hidden*, .directory, .Trash-*, .xsession-errors*, data/ 이다.
- 위 제외 경로/패턴과 그 하위 항목은 읽기, 검색, 수정, 삭제, 생성, 이동, 이름 변경, 포맷팅에서 모두 제외한다고 명시한다.
- evidence가 가리키는 제외되지 않은 실제 소스 또는 테스트 파일과, 대상 추상화의 직접 호출부 범위에서만 작업한다고 명시한다.
- 기본 실행은 approval_policy="never", sandbox_mode="workspace-write"를 전제로 하되, 실제 환경은 sandbox_mode="danger-full-access"일 수 있음을 반영해 추측성 탐색과 위험한 광역 수정은 금지한다고 명시한다.
- 작업 시작 후 후보의 과도한 추상화 여부와 제거해도 되는 이유를 현재 코드 기준으로 짧게 재확인하게 지시한다.
- 후보가 이미 단순화됐거나 evidence가 현재 코드와 맞지 않으면 무리하게 수정하지 말고 그 사실과 확인 결과를 보고하게 지시한다.
- 현재 후보에서 실제로 불필요하다고 확인된 추상화만 제거하고, 새로운 추상화, factory, strategy, wrapper, manager 계층으로 대체하지 않는다고 명시한다.
- 단순한 직접 호출, 구체 타입, 명확한 함수 구조를 선호하되 frontend/backend 책임 경계와 IPC 계약을 흐리지 않게 지시한다.
- 기존 동작, 공개 API, 사용자 흐름, 테스트 결과를 유지하고 동작 변경은 최소화한다고 명시한다.
- De-abstraction은 해당 후보가 가리키는 구조 문제 1건에만 집중하고, 무관한 버그 수정, 성능 최적화, 기능 추가, 스타일 정리는 제외한다고 명시한다.
- 가능하면 기존 동작 보존을 확인하는 최소 검증을 수행하게 지시한다.
- 테스트나 검증은 watch mode가 아닌 one-shot 명령으로 실행하고, 실행이 멈출 수 있는 명령에는 제한 시간을 두라고 지시한다.
- npm run dev, vite --host, vitest --watch, jest --watch처럼 종료되지 않는 명령은 사용하지 말고, 서버 실행이 꼭 필요하면 timeout 가능한 방식으로 짧게 smoke 확인한 뒤 반드시 종료하게 지시한다.
- 작업과 검증이 끝나도 커밋은 직접 수행하지 말고, 변경 내용과 검증 결과만 보고하게 지시한다.
- 최종 응답에는 변경 파일, 제거한 추상화, 핵심 수정 내용, 검증 결과, 남은 리스크를 보고하게 지시한다.
- "앞서 정리한", "위 공통 전제", "다음 래퍼" 같은 참조 표현은 사용하지 않는다.

입력 후보 JSON:
{{candidates_payload}}

응답 형식:
- JSON 객체 하나만 반환한다.
- 최상위 키는 `prompts` 하나만 사용한다.
- 각 항목은 candidate_id, title, prompt를 포함한다.
- prompts 배열 순서는 입력 후보 순서를 유지한다.
- candidate_id는 입력 후보 id와 정확히 같아야 한다.
- title은 최대 200자, prompt는 최대 32768자다.
- prompt는 한국어로 작성한다.
- 응답 예시는 아래 형태를 따른다. 예시 값은 실제 응답에서 그대로 사용하지 않고, 입력 후보에 맞게 작성한다.

응답 예시:
{
  "prompts": [
    {
      "candidate_id": 1,
      "title": "후보 제목",
      "prompt": "/goal 이 후보 작업은 새 Codex 세션에서 단독 실행된다. 입력 후보의 problem, evidence, priority, risk, impact를 반영해 지정된 과도한 추상화 1건만 단순화하고 검증 결과를 보고하라."
    }
  ]
}

