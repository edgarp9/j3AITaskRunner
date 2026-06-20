/goal 당신은 Rust 기반 애플리케이션에서 과도한 추상화를 걷어내는 데 강한 클린코드 아키텍트이다.

이 프로젝트에서 이미 과도하게 추상화된 코드를 찾아 주세요.
목표는 추상화를 줄이고, 코드 흐름을 읽기 쉽게 만들며, 기존 동작은 그대로 유지하는 것입니다.

단, 억지로 후보를 찾지는 마세요.
검토 결과 후보가 0건이어도 괜찮습니다.

버그 수정, 성능 최적화, 일반 스타일 개선, 단순 책임 분리 리팩토링은 모두 후보에서 제외한다.

제외 경로/패턴: dist/, .git/, .my/, build/, log/, target/, __pycache__/, *.pyd, *.pyc, *.pyo, *$py.class, .venv/, venv/, env/, ENV/, site-packages/, .ruff_cache/, .pytest_cache/, .mypy_cache/, .pyre/, .hypothesis/, .tox/, .nox/, .eggs/, *.egg-info/, pip-wheel-metadata/, .coverage, .coverage.*, coverage.xml, htmlcov/, *.log, *.tmp, *.bak, .vscode/, .idea/, .DS_Store, Thumbs.db, Desktop.ini, *~, *.swp, *.swo, .nfs*, .fuse_hidden*, .directory, .Trash-*, .xsession-errors*, data/

위 제외 경로/패턴과 일치하는 파일·디렉터리 및 그 하위 항목은 읽기, 검색, 후보 탐색, evidence 인용, 수정 후보 산정, 수정 지시문 생성 대상에서 모두 제외한다. 또한 해당 경로 내부에서는 파일 생성, 수정, 삭제, 이동, 이름 변경, 포맷팅을 수행하지 않는다.

후보 id는 1부터 순서대로 부여한다. 응답은 한국어로 작성한다.

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
      "problem": "확인한 과도한 추상화를 한 문장으로 설명한다.",
      "evidence": "제외되지 않은 실제 파일 경로, 대상 추상화 이름, 단일 구현/단순 위임/미사용 근거를 적는다.",
      "priority": "medium",
      "risk": "medium",
      "impact": "수정하지 않았을 때의 유지보수 영향을 적는다."
    }
  ]
}

