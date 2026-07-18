# 라이선스 문서

라이선스 파일, About/Licenses 표시, 공개 배포 준수 사항을 확인할 때 읽는다. 릴리즈 절차는 [`release.md`](release.md)를 따른다.

## 앱 버전과 About

- 앱 버전은 [`../app/version.py`](../app/version.py)의 `APP_VERSION`을 단일 기준으로 관리한다.
- 메인 창 제목, 설정 대화상자, About 창 상단 버전 라벨은 `APP_VERSION`을 표시한다.
- About 본문용 `about.txt`에는 앱 버전을 중복 명시하지 않는다.
- About 창은 소스 코드 링크 `https://github.com/edgarp9`와 읽기 전용 `about.txt` 본문을 표시한다.

## 배포물 포함 고지

배포물에는 다음을 포함한다.

- `LICENSE`
- `THIRD_PARTY_NOTICES.txt`
- `about.txt`
- `LICENSES/APACHE-2.0.txt`
- `assets/app_icon.ico`, `assets/app_icon.png`, `assets/app_icon.svg`
- 빌드 인터프리터 Python `LICENSE.txt`
- PyInstaller 라이선스
- `tkinterdnd2` 라이선스

필요한 라이선스 파일을 찾을 수 없으면 릴리즈 빌드는 실패해야 한다.

## Licenses 창 기준

- `Licenses` 창의 `Notice Inventory`는 배포물에 포함된 실제 외부 라이브러리와 리소스 목록과 맞아야 한다.
- 내부 배포 관리용 `Release Checklist`는 사용자용 `Licenses` 창에 표시하지 않는다.

## 공개 배포 리스크

- 공개 배포 전에 PyInstaller 산출물에 포함된 Python/Tcl/Tk/native library, OpenSSL, SQLite, zlib, expat, libffi, bzip2, XZ/liblzma, libmpdec, mimalloc, Zstandard, `tkinterdnd2`, `tkinterdnd2-universal`, tkDnD 파일과 `THIRD_PARTY_NOTICES.txt` 고지 대상이 맞는지 확인한다.
- GPL-3.0 공개 바이너리 배포는 실행 파일만 제공하면 부족하다. 해당 릴리스 버전에 대응하는 전체 소스 코드를 프로젝트 저장소/릴리스 소스 아카이브 또는 GPL-3.0이 허용하는 방식으로 함께 준비한다.
- PyInstaller가 수집한 패키지 `LICENSE`, `NOTICE`, `.dist-info` 메타데이터는 기본적으로 제거하지 않는다. 제거가 필요하면 같은 고지 내용을 `THIRD_PARTY_NOTICES.txt`나 별도 라이선스 파일로 보존한다.
- 앱 아이콘은 Google Material Symbols 계열 ECG waveform SVG에서 파생된 Apache-2.0 고지 대상으로 기록하고 `LICENSES/APACHE-2.0.txt`를 포함한다. 아이콘 교체 시 새 아이콘의 패밀리/글리프, 라이선스, 저작권 문구, 필요한 라이선스 파일을 다시 확인한다.
- 프롬프트 자산이 프로젝트 자체 제작물이 아니라면 공개 배포 전에 출처와 라이선스를 `THIRD_PARTY_NOTICES.txt`에 추가한다.
