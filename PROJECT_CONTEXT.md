# Project Context (PRD)

> 이 파일은 PRD(요구사항/제품 컨텍스트)를 정리하는 공간입니다.
> 필요 시 섹션을 자유롭게 추가하세요.

## 문제 정의
- 

## 요구사항
- PRD를 `PROJECT_CONTEXT.md`에 유지하고, 아키텍처/규칙은 `README.md`에 기록한다.
- 두 파일에 기록된 방향성과 단계에 따라 다음 작업을 이어서 수행한다.
- 시스템 구조/아키텍처 상세는 `docs/system_overview.md`에 기록하고, 요약을 `PROJECT_CONTEXT.md`에 반영한다.
- 수집 기사 목록은 카드형 피드 UI로 제공하고, 3열 그리드로 노출한다.
- “찜” 기능과 “찜한 기사” 메뉴/페이지를 제공한다.
- 이미지/로고가 없거나 로드 실패 시 토스 컬러 플레이스홀더를 보여준다.
- 상단에 PO/PM 관점 추천 트렌드를 롤링 형태로 노출한다.

## 목표
- 시스템 구조/아키텍처 문서화 및 코드 반영

## 비목표
- 

## 사용자/타겟
- 

## 핵심 기능
- 수집 기사 카드형 피드 노출(태그 및 추천 배지 포함)
- 찜/찜한 기사 관리(제거 시 읽음 처리)
- 트렌드 롤링 바(글로벌 트렌드 5개)

## 사용자 흐름
- 수집 기사 목록에서 기사 확인 → 찜 버튼으로 보관
- 상단 트렌드 바에서 글로벌 트렌드 빠르게 확인
- 찜한 기사에서 제거로 읽음 처리

## 데이터/모델
- `articles` 테이블에 수집 기사 저장
- `bookmarks` 테이블에 찜 상태 저장(removed_at으로 읽음 처리)

## 성공 지표
- 

## 범위/우선순위
- 

## 제약/리스크
- 

## 오픈 이슈
- PRD의 상세 내용(문제 정의/목표/핵심 기능 등) 미정

## 진행 단계
- `PROJECT_CONTEXT.md` 생성 및 PRD 섹션 템플릿 구성
- `README.md`에 프로젝트 메모리/응답 언어/연속 진행 규칙 기록
- `docs/system_overview.md` 템플릿 생성 및 설계안 초안 반영
- 카드형 피드 UI 및 찜/트렌드 바 기능 반영

## 다음 단계
- `docs/system_overview.md`의 빈 항목 구체화
- PRD 핵심 항목 채우기(문제 정의, 목표, 핵심 기능, 사용자 흐름)
- 트렌드 목록 자동 갱신 방식 결정(크롤링/캐시)

## 업데이트 메모 (2026-02-18)
- Railway 운영 구조 확정:
- `yong2`: API 서버 전용(`uvicorn app.main:app --host 0.0.0.0 --port $PORT` 유지)
- `crawler-cron`: `/crawl-all` 호출 전용 스케줄러
- 크론은 `crawler-cron`에서만 실행하고, `yong2`의 크론은 비활성화

## 내일 작업 우선순위 (2026-02-19)
- 1. 로그인 기능
- 2. 화면 UI/UX 개선
- 3. 설정 탭 화면 구성
- 4. 모바일 화면 노출 조건 정의 및 반영

## 작업 메모
- 로그인 기능은 인증 방식(JWT/세션), 보호 라우트 범위, 로그인/로그아웃 UX를 먼저 확정한다.
- 설정 탭은 계정/크롤링/알림/환경설정 섹션으로 IA를 분리해 설계한다.
- 모바일 노출 조건은 브레이크포인트별 컴포넌트 표시/숨김 규칙으로 문서화한다.

## 운영 전환 메모 (2026-02-18, i-boss)
- i-boss는 Railway 런타임에서 직접 크롤링하지 않고, 로컬 PC 수집 + 재배포 반영 방식으로 전환.
- 로컬에서 `data/iboss_manual.json`을 생성/업데이트 후 GitHub push.
- Railway 자동 배포 시 서버 startup 동기화로 DB 반영.

## 반영된 기능
- `POST /sync-manual-iboss` 수동 동기화 API 추가.
- startup 시 `data/iboss_manual.json` 자동 로드/삽입.
- `/crawl-all`에서 `i_boss`는 `manual_only`로 skip.

## 운영 절차 (Daily)
1. 로컬에서 i-boss 수집 실행
2. 결과 파일(`data/iboss_manual.json`) 커밋/푸시
3. Railway 자동 배포 확인
4. 로그에서 `[manual_iboss_sync]` 로드/삽입 건수 확인

## 자동화 스크립트
- `scripts/collect_and_push_iboss.ps1`
- 실행 예시: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/collect_and_push_iboss.ps1 -Limit 50`
