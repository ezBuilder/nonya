# 설계: 세션 자동복구 옵저버 — nonya (노냐)

> ⚠️ **갱신(2026-06-19)**: 구현이 **bash → Python 크로스플랫폼(Win+Mac)**으로 이행됨. 아래 "프로젝트 구조"의 `lib/*.sh`는 폐기되고 `nonya/`(코어) + `nonya/backends/`(OS별)로 대체. 현행 구조·근거는 [ARCH-cross-platform.md](ARCH-cross-platform.md), 연구는 [RESEARCH-auto-inject](RESEARCH-auto-inject-2026-06-19.md)·[RESEARCH-windows](RESEARCH-windows-auto-inject-2026-06-19.md). 본 PLAN은 최초 설계 의도(불변식·안전게이트·모드)의 기록으로 유지.

> 이름 유래: "놀고 있냐?" — 일 시켜놨는데 에러 뱉고 멈춰 있으면 노는 것. nonya가 그걸 감시·복구한다.
> 로마자: 노(no)+냐(nya) = **nonya**. 레포·폴더·CLI명 모두 `nonya`.

## Context
밤새 자율 작업을 돌려두면 Claude/Codex 세션이 과부하("서비스가 사용 중입니다")·API오류·무응답·크래시로 **조용히 멈춰** 있다. 사용자가 원하는 복구는 헤드리스 resume(창 밖 진행)이 **아니라**, 지금 떠 있는 그 창(App/CLI)에 **다시 명령을 날려 같은 대화를 그 자리에서 이어가게** 하는 것이다. 단, 구독 과금 유지(추가비용 0)가 전제.

## 확정된 설계 결정 (사용자 승인 완료)
1. **감지 = 하이브리드.** transcript 파일 idle(mtime)로 1차 트리거 → ②transcript 내용 분류 + ③대상 창 스크린샷 OCR 로 "정말 멈춤/에러"임을 **확정한 뒤** 행동.
2. **재시도 = 모드 분리.** 기본 `--on-error`: 에러/크래시/rate-limit일 때만 재주입. `--auto`: 멈추면 무조건 `<<DONE>>` 신호까지 계속.
3. **창 타겟팅 = 안전 우선.** 단일창이고 타겟 확신될 때만 키 주입. 다중창·타겟 불확실이면 **주입 금지·알림만**.
4. **Codex 앱 = 알림만.** AX로 창 핸들이 안 잡혀 인-윈도우 주입을 확신할 수 없으므로 감지+알림만. (CLI 단일 터미널창은 주입 대상 가능.)
5. **형태 = 독립 프로젝트 + 통합 심.** 배포 가능한 독립 레포(`~/workspace/nonya`, CLI명 `nonya`). `install.sh` 심이 SessionStart/End 훅을 `settings.json`에 배선해 자동 기동 제공. Code Brain과 코드베이스 분리.

## 탐색으로 검증한 현실 제약 (설계 근거)
- **transcript 내용 판정은 신뢰도 높음.**
  - Claude `~/.claude/projects/<proj>/<session_id>.jsonl`: 에러 **명시 기록** — `.error`(rate_limit/server_error/model_not_found…), `.apiErrorStatus`(429/503), `.isApiErrorMessage:true`. 완료 `.message.stop_reason=="end_turn"`, 도구중 `=="tool_use"`.
  - Codex `~/.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl`: 완료 `event_msg/task_complete`, 과부하 `token_count…rate_limits.rate_limit_reached_type != null`, 진행중 `turn_context.realtime_active==true`. **전용 에러 이벤트 없음** → rate-limit 필드 + task_complete 부재 + idle로 추론.
  - 앱·CLI가 `~/.codex/sessions` **공유**(`session_meta.payload.originator`로 출처 구분).
- **두 앱 다 Electron/Chromium → 접근성(AX)으로 화면 텍스트·스피너·입력상태 읽기 불가.** "화면 확정"은 **screencapture + tesseract OCR**로만 가능(화면 녹화 권한 필요).
- **창↔세션 매핑 불가.** 창 제목 전부 "Claude"/"Codex". Claude 앱은 AX로 창 개수/AXMain 읽힘 → **단일창 안전 판정 가능**. Codex 앱은 AX 창 **0개** → 판정 불가 → 알림만.
- 도구: `tesseract`·`screencapture`·`shortcuts`·`jq` 있음. `cliclick`·`hammerspoon`·`gdate` **없음**(BSD `date -j -f`, 키전송은 osascript).
- 세션 식별: `CLAUDE_CODE_SESSION_ID` env + 경로로 transcript 특정 → SessionStart 훅에서 기동 가능.

## 아키텍처 (폴링 루프, 단계별 게이트)
```
poll(매 ~15s):
  1) locate transcript (claude: 세션ID경로 | codex: cwd/originator 최신 rollout)
  2) idle = now - mtime;  idle < IDLE → 계속 폴링
  3) classify(transcript tail) → STATE ∈ {ERROR, RATE_LIMIT, COMPLETED, TOOL_PENDING, IDLE_WAIT}
  4) decide(mode):
       --on-error: STATE∈{ERROR,RATE_LIMIT} 또는 (TOOL_PENDING & idle>HANG_CAP)
       --auto    : 위 + {COMPLETED,IDLE_WAIT} (단 <<DONE>> 있으면 종료), TOOL_PENDING은 HANG_CAP까지 대기
  5) confirm(OCR): 대상 창 screencapture → tesseract → 에러배너/정지 확인(생성중이면 중단). 권한없으면 알림만
  6) target-gate: 앱 창수 확인
       claude 1창 & AX접근 & Accessibility권한 → 주입 OK
       그 외(다중창/codex앱/CLI다중pane/권한없음) → 알림만(STUCK)
  7) inject: 클립보드백업 → set clipboard nudge → activate/AXRaise → Cmd+V → send-key → 클립보드복원
  8) verify: GRACE 내 mtime 증가=복구. 무진행 누적 STUCK_AFTER → 강한 알림
  9) done: <<DONE>> 또는 정상복귀 → 완료 통지/종료
```
**안전 불변식: 6번 게이트 미통과 시 절대 키 전송 안 함(알림만).** 오주입보다 미복구가 안전.

## 신뢰도 매트릭스
| 대상 | 동작 |
|---|---|
| Claude 앱, 단일창 | 감지+OCR확정+자동주입 ✅ |
| Claude 앱, 다중창 | 알림만 |
| Codex 앱 | 알림만(AX창 0) |
| CLI, 전용 단일 터미널창 | 자동주입 |
| CLI, 다중 pane/tmux | 알림만 |
| 권한 없음 | 알림만 + 권한 안내 |

## 프로젝트 구조 (독립 레포 + 통합 심)
- `bin/nonya` — 메인 옵저버 루프.
- `lib/` — detect.sh(분류) / target.sh(창 게이트) / confirm.sh(OCR) / inject.sh(AppleScript) / notify.sh / perms.sh.
- `hooks/sessionstart-spawn.sh`, `hooks/sessionend-stop.sh` — 세션ID pidfile 자동 기동/종료.
- `install.sh`/`uninstall.sh` — 통합 심: `~/.local/bin` 심볼릭 + settings.json 훅 멱등 배선 + launchd 옵션.
- `launchd/com.user.nonya.plist.template` — 영속화.
- `docs/PLAN.md`, `README.md`, `LICENSE`(MIT), `tests/`.

## 권한 (1회)
- **손쉬운 사용(Accessibility):** 키 주입. 미보유 → 알림만.
- **화면 녹화(Screen Recording):** OCR screencapture. 미보유 → OCR 생략/알림만.

## 검증 (end-to-end)
1. 정적: `bash -n`, `osacompile`, `plutil -lint`.
2. `nonya --check` → 권한/도구 리포트.
3. `--dry-run` → 감지→분류→게이트만, 키전송 0.
4. 분류 단위테스트(jq, 에러 포함 fixture).
5. 통제된 라이브 1회(단일 Claude 창, 입회).
6. 다중창/Codex앱 → 알림만 빠지는지(오주입 0).
7. install/uninstall 멱등·원복.

## 비목표 / 한계
- 인증·과금·rate-limit 소진은 재주입으로 안 풀림 → STUCK 알림.
- Codex 앱 인-윈도우 자동복구 미제공(알림만).
- 주입 시 대상 창 포커스 가져감(야간 무인 전제). idle 임계로 응답 도중 안 끼어듦.
