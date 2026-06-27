# nonya 타깃 지원 매트릭스 (2026-06-20)

목표: Claude App / Claude CLI / Codex App / Codex CLI 4종 상용화급 완벽 지원.

탐지는 App·CLI가 같은 on-disk JSONL을 공유하므로 엔진당 1개 경로. 주입은 CLI/tmux, raw terminal, GUI 앱으로 갈린다. 상세 동작 방식은 [HOW-IT-WORKS.ko.md](HOW-IT-WORKS.ko.md)를 기준으로 한다.

| 타깃 | 탐지 | 주입 경로 | 검증 상태 |
|------|------|-----------|-----------|
| Claude App | `~/.claude/projects/*/*.jsonl` (stop_reason/error/429) | unattended일 때 ScreenCaptureKit + Vision OCR로 사이드바/헤더를 읽고 대상 대화를 증명한 뒤 좌표 클릭 + Cmd+V + Enter + OCR 검증. 단일 세션 직접 앱 주입/스모크 테스트는 `NONYA_ALLOW_REAL_APP_INJECT=1` 필요 | 탐지✓ TOOL_PENDING 실측 / read-only 게이트 ok(창1) / 직접 inject-test 기본 차단✓ |
| Claude CLI | 동일 JSONL | tmux `find_pane('claude')` → send-keys -l + Enter | 탐지✓ / tmux 주입 e2e✓(스로어웨이 페인) |
| Codex App | `~/.codex/sessions/Y/M/D/rollout-*.jsonl` (task_complete/started, rate_limits) | Claude와 동일한 GUI OCR/좌표 경로. Codex thread deep-link 전환은 별도 지원. 단일 세션 직접 앱 주입/스모크 테스트는 `NONYA_ALLOW_REAL_APP_INJECT=1` 필요 | 탐지✓ COMPLETED 실측 / read-only 게이트 ok(창1) / 직접 inject-test 기본 차단✓ |
| Codex CLI | 동일 rollout JSONL | tmux `find_pane('codex')` → send-keys | 탐지✓ / tmux 경로 동일(검증✓) |

## 검증 근거 (2026-06-26, 이 머신)
- 4종 검증 표면: Claude.app/Codex.app read-only AX 게이트, Claude/Codex CLI tmux disposable pane, macOS GUI disposable `NonyaProbe`.
- 주입 메커니즘: `MacBackend.inject('NonyaProbe', marker)` → 클립보드 붙여넣기 + Return + 클립보드 복원, marker 도착 확인.
- App 게이트(2026-06-26 재검증): **Claude.app = `ok`(창1), Codex.app = `ok`(창1)**. 실계정 앱 키입력은 기본 차단하고 알림-only로 처리한다. `NONYA_ALLOW_REAL_APP_INJECT=1` 명시 시에만 window_gate ok 뒤 주입 경로를 탄다.
- macOS GUI 주입 경로(2026-06-26 재검증): 전용 disposable `NonyaProbe` 앱으로 다중창 게이트 차단(키 0) + 단일창 Cmd+V 전달(마커 도착) 확인. 실계정 Claude/Codex에는 키를 보내지 않는다.
- CLI 경로: 가짜 ERROR 트랜스크립트 + tmux 페인 → 넛지 텍스트 주입 + Enter 전달 확인.
- `tmux.find_pane(engine)`: 엔진명을 페인 foreground 명령 + 프로세스 서브트리에서 매칭. tmux 밖이면 None → graceful 알림.

## 안전 불변식
- 게이트 != ok → 키 0개(알림만). App 다중창 → multi-window 알림만(세션 매핑 불가).
- 기본 감시는 화면 캡처가 아니라 JSONL/log 기반이다. GUI OCR은 대상 대화 선택, composer 입력 확인, 전송 확인에만 사용한다.
- raw terminal split은 AX로 split을 찾아도 키 이벤트가 앱의 활성 split으로 갈 수 있어 기본 알림-only다. 연구용 `NONYA_AX_SPLIT=1` 외에는 자동복구 경로로 보지 않는다.
- 주입 텍스트 = `policy.DEFAULT_NUDGE`(전문적 한국어, 이모지 0). 잔소리는 사용자에게만 보임.

## 하드닝 완료 (2026-06-20, 8개 서브시스템 감사 → 적대검증 → 수정)
- **탐지 Codex**: 활성 턴이 80줄 tail 밖이라 IDLE_WAIT 오분류 → 넓은 스캔 + 마지막 task 마커 위치로 판정(STALLED/COMPLETED). 회귀 테스트 추가.
- **탐지 Claude**: end_turn 뒤 큐된 프롬프트를 COMPLETED로 오판(재개 중 오주입) → user-side 레코드가 더 최신이면 TOOL_PENDING. 서브에이전트(isSidechain) 제외. 회귀 테스트 추가.
- **루프 무인안전**: (1)에스컬레이션이 매 사이클 텔레그램/슬랙 폭주 → cooldown(기본 600s) 쓰로틀. (2)죽은 세션에 12h 주입 → `give_up_after`(기본 9)서 중단+stopped. (3)에러 churn을 거짓 "복구"로 판정 → mtime 점프 시 재분류해 ERROR/RATE_LIMIT/STALLED면 진행 아님. 회귀 테스트 3종.
- **CLI 안전**: find_pane이 모호(0/다수 매칭) 시 추측 금지→None(오주입 방지), 정확 매칭 우선. tmux copy-mode 페인은 `pane-busy` 게이트(키 오라우팅 차단). 회귀 테스트.
- **Swift 주입 테스트**: 다중창 게이트(`ABORT-windows`) 추가 — 백엔드와 동일하게 미스파이어 방지.
- **Swift UI**: `stopped` 눈 색 추가 + 코어 프로세스 종료 감지 → 메뉴 복귀(좀비 'watching' 제거).
- 테스트 11 → 27, ALL PASS.
- **유니버설 바이너리**: Swift shell(`swift build --arch arm64 --arch x86_64`) + Python core(PyInstaller `--target-arch universal2`, universal2 python 감지+폴백) → 둘 다 `x86_64 arm64`. `build/NonyaPet.app` Intel+Apple Silicon 동작. build-app.sh가 조립 후 lipo 검증 출력.

## 남은 상용화 과제 (대형 — 컴파일드 헬퍼/특수환경 필요)
- **App 다중창 세션 매핑**: 현재 다중창=알림만(안전). 세션별 창 타깃팅은 `_AXUIElementGetWindow`(private) + CG 매핑 컴파일드 헬퍼 필요(research §5). 고신뢰 매칭일 때만 주입, 아니면 알림만 유지.
- **Electron AX 강제**: `AXManualAccessibility=true`로 Chromium AX 트리 강제(Codex 0창 케이스 대비) — 컴파일드 헬퍼.
- **CLI 비-tmux 경로**: iTerm2 Python API(async_send_text, 세션 정확) / 기타 터미널 단일탭 게이트. 현재 tmux만 검증.
- **OCR busy-게이트**: tesseract 없으면 비활성(advisory) → check()에 경고 + Apple Vision 헬퍼 권장.
- **실제 에러 레코드 캡처**: Claude/Codex ERROR/RATE_LIMIT 필드는 가정 → 실샘플로 핀.
- **Windows/WSL CLI**: 미구현 — 라벨 정정 + wsl.exe tmux 션트(검증 필요).
- **패키징**: 코드사이닝/공증 + Accessibility/Screen-Recording 권한 온보딩 + 자동시작 + 아이콘.
