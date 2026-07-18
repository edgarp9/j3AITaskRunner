# 릴리즈 문서

PyInstaller 릴리즈 빌드, 산출물 구조, 배포 전 검증 기준을 확인할 때 읽는다. 라이선스와 고지 기준은 [`license.md`](license.md)가 소유한다.

## 릴리즈 빌드

```bash
python build_release.py
```

- PyInstaller `onedir` 방식만 사용한다.
- 산출물 루트는 `dist/<플랫폼이름>/j3AITaskRunner/`다.
- 실행 파일은 산출물 루트, 라이브러리와 번들 리소스는 `lib/` 아래에 둔다.
- `build_release.py`는 플랫폼별 `.build-venv/<플랫폼이름>/`를 준비하고 PyInstaller 6 이상과 `tkinterdnd2`를 설치한 뒤 venv Python으로 재실행한다.
- Windows와 Linux venv는 공유하지 않는다.
- 빌드 완료 후 산출물 루트를 OS 파일 탐색기로 연다.
- 릴리즈 빌드는 별도 소스 ZIP을 생성하거나 남기지 않는다. 플랫폼 산출물 폴더의 `j3AITaskRunner-*-source.zip`은 빌드 시작 시 정리한다.

## 릴리즈 검증

릴리즈 관련 변경 뒤 권장 검증:

```bash
python -m unittest tests.test_build_release tests.test_license_notices tests.test_ui_resources tests.test_main
```

공개 배포 전 확인:

- 전체 검증과 UI smoke를 통과한다.
- 실제 PyInstaller 산출물을 생성한다.
- CI 기준 Python 3.12 결과를 확인한다.
- 산출물에 포함된 라이선스와 고지가 [`license.md`](license.md)의 기준과 일치하는지 확인한다.

## 배포물 기준

- 공개 바이너리 배포 시 해당 릴리스에 대응하는 전체 소스 코드를 프로젝트 저장소, 릴리스 소스 아카이브, 또는 GPL-3.0이 허용하는 방식으로 함께 준비한다.
- PyInstaller가 수집한 패키지 `LICENSE`, `NOTICE`, `.dist-info` 메타데이터는 기본적으로 제거하지 않는다.
- 제거가 필요하면 같은 고지 내용을 `THIRD_PARTY_NOTICES.txt`나 별도 라이선스 파일로 보존한다.
