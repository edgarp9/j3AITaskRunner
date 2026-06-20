/goal 당신은 Python 및 Tkinter 기반 애플리케이션의 성능 최적화에 강한 소프트웨어 아키텍트이다.

후보는 아래 닫힌 집합에서만 찾는다. 목록 밖의 문제 유형, 버그 수정, 일반 구조 개선, 스타일 개선, 리팩토링은 모두 제외한다. 비슷해 보여도 아래 항목에 명확히 해당하지 않으면 후보로 만들지 않는다.

- 반복되는 불필요한 파일 I/O 또는 네트워크 I/O
- 비효율적인 반복 연산
- 동일 데이터에 대한 중복 파싱, 직렬화, 정렬, 변환
- 불필요한 전체 재계산
- 불필요한 UI 전체 갱신 또는 잦은 렌더링
- tkinter 메인 루프를 오래 점유하는 동기 작업
- 불필요한 polling, sleep, busy wait
- 대량 데이터 처리의 비효율
- 메모리 사용 급증 또는 캐시 운용 비효율
- 이미지, 텍스트, 리스트, 테이블의 과도한 재구성
- 불필요한 subprocess 호출
- 리소스 해제 지연으로 인한 누적 성능 저하

목록에 명확히 해당하고 실제 파일 근거가 충분한 경우에만 후보로 제시한다.
목록 밖은 제외하며, 근거가 약하거나 해석 의존이 큰 경우는 제외한다.
같은 원인의 파생 현상은 가능하면 하나로 묶고, 억지로 후보 수를 늘리지 않는다.
0건이어도 무방하다.

evidence 확보에 필요한 최소 파일만 확인하며 저장소 전역 탐색은 하지 않는다.
실제 파일 근거 없는 추측은 금지한다.
evidence에는 제외되지 않은 실제 소스 또는 테스트 파일 경로와 확인 내용을 포함한다.

각 후보는 problem, evidence, priority(high/medium/low), risk(critical/high/medium/low/minimal), impact를 정리한다.
risk는 방치 시 성능 저하 위험도로 판단한다.

- critical: 심한 UI 멈춤, 메인 루프 블로킹, 메모리 급증, 사실상 사용 불가
- high: 핵심 화면 지연, 반복 작업의 큰 성능 저하, 큰 리소스 낭비
- medium: 체감 지연, 비효율 반복 연산, 불필요한 I/O
- low: 경미한 성능 저하, 드문 지연
- minimal: 가독성, 중복, 경미한 개선

제외 경로/패턴:
dist/, .git/, .my/, build/, log/, __pycache__/, *.pyd, *.pyc, *.pyo, *$py.class, .venv/, venv/, env/, ENV/, site-packages/, .ruff_cache/, .pytest_cache/, .mypy_cache/, .pyre/, .hypothesis/, .tox/, .nox/, .eggs/, *.egg-info/, pip-wheel-metadata/, .coverage, .coverage.*, coverage.xml, htmlcov/, *.log, *.tmp, *.bak, .vscode/, .idea/, .DS_Store, Thumbs.db, Desktop.ini, *~, *.swp, *.swo, .nfs*, .fuse_hidden*, .directory, .Trash-*, .xsession-errors*, data/

위 제외 경로/패턴과 일치하는 파일·디렉터리 및 그 하위 항목은 읽기, 검색, 후보 탐색, evidence 인용, 수정 후보 산정, 수정 지시문 생성 대상에서 모두 제외한다. 또한 해당 경로 내부에서는 파일 생성, 수정, 삭제, 이동, 이름 변경, 포맷팅을 수행하지 않는다.

후보 id는 1부터 순서대로 부여한다.
응답은 한국어로 작성한다.

응답 형식:
- JSON 객체 하나만 반환한다.
- 최상위 키는 `candidates` 하나만 사용한다.
- `candidates`는 배열이며, 후보가 0건이면 빈 배열 `[]`를 반환한다.
- 각 후보 객체는 `id`, `title`, `problem`, `evidence`, `priority`, `risk`, `impact`를 포함한다.
- `priority`는 `high`, `medium`, `low` 중 하나만 사용한다.
- `risk`는 `critical`, `high`, `medium`, `low`, `minimal` 중 하나만 사용한다.
- JSON 바깥의 설명 문장, Markdown 코드블록, 제목, bullet을 추가하지 않는다.
- 응답 예시는 아래 형태를 따른다. 예시 값은 실제 응답에서 그대로 사용하지 않고, 실제로 확인한 근거만 사용한다.

응답 예시:
{
  "candidates": [
    {
      "id": 1,
      "title": "후보 제목",
      "problem": "확인한 문제를 한 문장으로 설명한다.",
      "evidence": "제외되지 않은 실제 파일 경로와 확인 내용을 적는다.",
      "priority": "medium",
      "risk": "medium",
      "impact": "수정하지 않았을 때의 영향을 적는다."
    }
  ]
}
