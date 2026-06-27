# nonya 방향: Progress & Correctness Supervisor (2026-06-20)

딥 리서치(6각도 + 2 전략가 + 최종결정, docs 워크플로) 결론. 캐릭터/레이저 같은 스펙터클은 폐기 — **실체(판단)**로 간다.

## 비전
nonya는 "계속해" 꽂는 keep-alive 봇이 아니라 **진척·정합성 감독관**이다. 에이전트의 on-disk 트랜스크립트를 읽어 **진짜·정확한 작업이 일어나는지 판단**하고, 일반 넛지 대신 트랜스크립트에서 계산한 **구체적 교정**을 주입하며, 프로젝트 자체 체크가 실제로 통과하기 전엔 "완료"를 안 믿는다. 그 위에 **신뢰 계층**(주입한 모든 키스트로크의 변조불가 원장 + 자기 전 한 제스처로 거는 자율 예산)을 둬서 "터미널에 자동으로 타이핑하는 앱"을 "밤새 맡겨도 되는 도구"로 바꾼다. 메뉴바 눈은 마스코트가 아니라 **4상태 글랜스 채널**.

근거: Anthropic Agent View/Channels + 32개 레포 전부 **관찰·중계**만 함(사람이 판단·행동). **판단하는 자율 감독관 = 미점유 화이트스페이스.**

## 킬러: 정합성 감독관 (verify-before-done + 교정 주입)
idle/완료선언 시 → 트랜스크립트에서 주장 추출 → 프로젝트 실제 체크 실행(test/lint/build 자동탐지) → "계속해" 대신 정확한 실패를 주입("테스트 통과했다며? pytest 3개 실패 — 그거 먼저"). 로컬 모델(옵션)로 비용0·오프라인, 없으면 고정넛지 폴백. 32개 레포 중 미점유(전부 고정/사람입력 주입).

## 랭킹 기능
1. **정합성 감독관** — 완료검증+교정주입 (notable, 중)
2. **Trust Ledger** — 주입 키스트로크 해시체인 감사로그 (breakthrough, 하)
3. **4-상태 분류** — stuck/done/waiting/looping 차등(루프엔 절대 넛지X) (notable, 중)
4. **Wake-Up 브리핑** — 검증된 야간 after-action 리포트 (notable, 하)
5. **자율 예산('목줄')** — 자기 전 범위·복구횟수·조용한시간 한 제스처 (incremental, 하)
6. **어텐션 라우터** — N세션 "나 필요함>looping>.." 우선순위 1줄 (incremental, 하)
7. **위험점수 자동승인** — 저위험 권한 자동허용, 파괴/비밀/배포 보류+에스컬 (notable, 중)
8. **에스컬레이션 사다리** — 진짜 결정만 폰으로(+자유텍스트 리다이렉트) (incremental, 중)
9. **레이트리밋 페이싱** — 429는 재넛지 말고 "HH:MM 재개" (incremental, 중)

## UX 원칙 (마스코트 아님)
- 눈 = 상태 채널: 5스타일이 4상태 어휘(감음=완료, 아래봄=나필요, 좁힘=넛지, 좌우=루프). 빨간 pill은 나 필요할 때만 숨쉼. reduce-motion 대응.
- 글랜스 우선, 드릴다운은 요청 시. "뭔가 막혔나 + nonya가 처리했나?"를 1초 안에.
- 미스터리 키 금지: 모든 주입 미리보기(편집+5초 카운트다운)·기록.
- 판단 근거 1줄 노출 + 임계값 튜닝(가상 발화 빈도 프리뷰).
- 자기 전 '목줄' 한 제스처로 범위·예산·조용한시간; 모델/권한 없으면 고정넛지로 폴백하고 그걸 말함(조용히 과행동 금지).
- 키보드/CLI/Raycast first.

## 폐기
3D 캐릭터, 레이저(둘 다 사용자 거부) · 라이브 터미널 미러 대시보드(Agent View 소유) · 원격호스트 감독(나중).

## 구현 상태 (2026-06-20)
DONE + 검증:
- 모듈 5종(`supervise/verify/ledger/corrective/briefing`) — 멀티에이전트 병렬 구현+적대리뷰, 단위테스트 103 assertion PASS.
- `loop.py` 통합 = **4-상태 라우팅**: waiting→절대 넛지X 에스컬레이션 / looping→멈춤+에스컬 / done→**verify 후 통과해야 인정, 실패면 구체 교정 주입** / stuck→교정 주입. 모든 개입 ledger 기록.
- **킬러 e2e 검증**: 가짜 "완료" 주장 + 실패 체크 → verify FAIL 잡고 `You said "..." but verification failed: ...` 교정 생성 + 원장 기록(체인 유효).
- cli: `--briefing`(야간 리포트, 검증됨), `--no-verify/--check-cmd/--project-dir/--model-cmd`.
- Swift 눈: waiting(노랑=나필요)/looping(보라=이상) 색 추가.
- 기존 안전(idle/user-idle/OCR/window 게이트·give-up·escalate 쓰로틀) 전부 보존, 전 테스트 PASS.

2차 DONE (2026-06-20, 기능모듈 5종 멀티에이전트 병렬+적대리뷰 → 통합):
- **budget.py** 자율예산'목줄': budget.json opt-in(없으면 기존 주입동작 유지) → alert-only/spend-ceiling/give-up 캡/panic-word 즉시중단/quiet-hours 소리억제. loop 통합·검증.
- **router.py** 어텐션 라우터: 세션별 `sessions/<pid>.json` + 우선순위 랭킹(waiting>looping>stuck>verify-failed>working>done). `nonya --router` JSON.
- **unblock.py** + `hooks/nonya-approve.py` 위험점수 자동승인: 저위험 reversible=allow, 파괴/비밀/배포/설치/네트워크/권한=ask(보류). 안전 기본 보류. 훅 실측 검증.
- **remote.py** 폰 에스컬레이션: escalate→ntfy/Telegram(비밀 redact). give-up 시 `poll_reply`로 폰 자유텍스트 지시 회신→주입 재개.
- **pacing.py** 레이트리밋 페이싱: 429는 재넛지 금지, `resume_at` HH:MM 스케줄.
- 테스트 11파일 330 assertion 전부 PASS, Swift 빌드 OK.

남은 폴리시(후속): Swift 멀티세션 메뉴바 집계(--router 소비)·눈 모션 인코딩·레이저/캐릭터 잔존 코드 완전 삭제(현재 비활성).

## 하드 불변식 (구현 전반)
핫패스 네트워크 0 · 불확실하면 안전쪽(미스파이어 0) · 비밀 redact · stdlib-only · 트랜스크립트 read-only.
