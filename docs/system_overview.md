# System Overview

## 목적
- 이 문서는 시스템 구조/아키텍처를 관리하는 단일 출처(Single Source of Truth)입니다.
- 상세 설계는 여기에 기록하고, 요약은 `PROJECT_CONTEXT.md`에 반영합니다.

## 시스템 범위
- sources.yaml 기반으로 수집 대상(Source)을 관리
- /crawl 이 source_id 를 받아 해당 source 규칙으로 크롤링
- 1차 대상: 요즘IT

## 상위 아키텍처
- 현재 구조: yong2 폴더, FastAPI + SQLite
- 구성 파일: `sources.yaml` (수집 대상과 규칙)

## 주요 컴포넌트
- API 서버: FastAPI (`app/main.py`)
- 크롤러: HTML/RSS 수집 로직 (`app/crawler.py`)
- 저장소: SQLite (`app/database.py`)
- 모델: 데이터 모델 (`app/models.py`)
- 소스 레지스트리: `sources.yaml` 로딩/검증/조회 (신규 컴포넌트)

## 데이터 흐름
- 클라이언트가 `/crawl`에 `source_id` 요청
- 서버가 `sources.yaml`에서 대상 규칙 로드
- 크롤러가 목록 페이지 파싱 후 개별 아티클 수집
- DB에 `crawl_runs` 기록 후 `articles` 저장
- 응답에 수집 결과 요약 반환

## 외부 연동
- 대상 사이트(요즘IT, 아이보스) HTTP 요청

## 배포/운영
- 로컬 개발 기준 `uvicorn app.main:app --reload`

## 보안/권한
- 초기 버전은 인증/권한 없음
- 크롤링 대상의 robots.txt 및 이용 정책 준수 필요

## 로드맵/변경 이력
- 해야 할 일:
- 1) 프로젝트 루트에 `sources.yaml` 스펙 확정 (요즘IT, 아이보스 포함, selector는 TBD)
- 2) V2 구조(파일/DB/흐름) 정리
- 3) DB 스키마 초안 제안
- 4) 설계 완료 후 코드 변경 체크리스트를 README에 추가
- 완료 조건:
- `sources.yaml` + `docs/system_overview.md` + `README.md` 업데이트 반영
- 코드는 아직 크게 바꾸지 않음
- 진행 방식:
- 먼저 설계안을 docs에 정리하고, 그 다음 코드 반영

## V2 파일 구조(제안)
- `sources.yaml` 수집 대상 및 규칙
- `app/main.py` API 라우트(`/crawl` 입력 변경)
- `app/source_registry.py` sources.yaml 로딩/검증
- `app/crawler.py` source 규칙 기반 수집
- `app/database.py` DB 연결/마이그레이션
- `app/models.py` 데이터 모델

## DB 스키마 초안
- sources
- id (TEXT, PK)
- name (TEXT)
- type (TEXT)
- start_url (TEXT)
- rules_json (TEXT, JSON 문자열)
- created_at (TEXT)
- updated_at (TEXT)
- articles
- id (INTEGER, PK)
- source_id (TEXT, FK -> sources.id)
- title (TEXT)
- url (TEXT, UNIQUE)
- content (TEXT)
- summary (TEXT)
- tags (TEXT, JSON 문자열)
- author (TEXT)
- image_url (TEXT)
- published_at (TEXT)
- created_at (TEXT)
- crawl_runs
- id (INTEGER, PK)
- source_id (TEXT, FK -> sources.id)
- started_at (TEXT)
- finished_at (TEXT)
- status (TEXT)
- error_message (TEXT)
- article_count (INTEGER)
