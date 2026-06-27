# 연구: 4개 타겟 자동 인-윈도우 주입 완벽동작 (2026-06-19)

> 목표: nonya를 **Claude 앱 · Codex 앱 · Gemini 앱 · CLI(터미널/tmux)** 4개 타겟 모두에서
> 'alert-only(알림만)'가 아니라 **자동 인-윈도우 주입(auto-inject)**으로 동작시키는 방법을 출처와 함께 정리.
> 출처: deep-research 하베스트 (6개 검색각도 → 30소스 페치 → 135주장 추출 → 25주장 3표 적대검증 → 19 confirmed / 6 killed). run `wf_a216c9b3-5df`.
> ⚠️ 검증 수위 표기: **[검증]**=3표 적대검증 통과, **[기각]**=검증 실패(아래 §7), **[가정]**=PLAN의 기존 전제로 이번에 재검증 안 됨.

---

## 1. 한 줄 결론 (신뢰도 티어)

자동 주입 신뢰도는 **CLI(iTerm2) > Claude 앱 ≈ Gemini 앱 > Codex 앱** 순서다.

- **CLI는 포커스 독립·세션 단위 주입이 가능해 가장 확실** — iTerm2 Python API(`async_send_text`)는 session_id로 특정 세션을 직접 타겟, tmux는 `send-keys -t <pane>`. 창 매핑 문제 자체를 우회. **[검증]**
- **Electron 3종(Claude/Codex/Gemini)은 같은 파이프라인**으로 끌어올린다: ①AX 트리 강제 활성화(`AXManualAccessibility`) → ②CG+AX 창 매핑 → ③Apple Vision OCR로 화면 상태 확정 → ④클립보드 붙여넣기 주입. 키 유니코드 직접 주입은 macOS 12+에서 조용히 실패하므로 **반드시 붙여넣기**. **[검증]**
- **Codex 앱(AX 창 0개)이 유일한 미해결 난점** — 위 파이프라인의 ①이 Codex에 먹히는지 미확인. 이게 핵심 리스크. **[미해결]**
- **Gemini CLI/앱의 온디스크 상태 스키마는 문서화돼 있지 않음** → 상태 분류는 리버스엔지니어링 또는 mtime-idle 폴백에 의존. **[검증]**

> ⚠️ **아키텍처 함의(중요)**: ①AX 강제활성화, ②`_AXUIElementGetWindow`(private) 창 매핑, ③Vision OCR(바운딩박스) 는 **순수 bash+osascript로 불가** — 작은 **컴파일드 헬퍼(Swift/Obj-C 바이너리)** 가 필요하다. 현재 nonya는 osascript 기반이라, Electron 자동주입을 원하면 이 헬퍼 도입이 전제다.

---

## 1.5 로컬 실측 보정 (이 머신, 2026-06-19) — **[실측]**

> deep-research는 웹 기준이라 일반론이다. 이 머신에서 직접 파일/도구를 실측해 아래를 **확정·보정**함. (구조·키·enum만 확인, 본문 미덤프.)

- **"Gemini 앱"의 실체 = Google Antigravity** (`/Applications/Antigravity.app` + `Antigravity IDE.app` + `antigravity-cli`). 보고서의 "Gemini" 전부 Antigravity로 읽을 것.
- **Antigravity 감지 스키마 = 확보(연구 최대공백 해소)**:
  - 대화: `~/.gemini/antigravity-cli/conversations/<uuid>.db` = **SQLite**(JSONL 아님). 테이블 `steps(idx, step_type:int, status:int, error_details:blob, task_details, step_payload, …)`, `trajectory_meta(trajectory_id, trajectory_type, source)`.
  - **상태 분류 = 최신 `.db`의 마지막 `steps.status`(정수 enum) + `error_details` non-null 여부**. `.db` mtime = idle 트리거.
  - 보조: `~/.gemini/antigravity-cli/cli.log`(→`log/cli-*.log`) 텍스트 로그에 **HTTP 코드 실재** (이 머신: `503`×10, `429`×1, `error`×28, `done`×4, `complete`×2) → grep 폴백 가능.
  - `history.jsonl` = 프롬프트 이력만(`display,timestamp,workspace`) → 상태분류 부적합.
  - ⚠️ 앱/IDE(`~/.gemini/antigravity/`, `antigravity-ide/`)의 `conversations/`는 이 머신에서 **비어 있음** → **GUI 앱 대화 저장 위치/포맷은 미해결**(CLI만 사용된 상태).
- **Claude 감지 = 구조 검증됨 [실측]**: 이 프로젝트 세션 jsonl에서 `.type`={assistant,user,system,attachment,mode,queue-operation,last-prompt}, `.message.stop_reason`={`end_turn`×11, `tool_use`×46} 실재. (정상 세션이라 에러 레코드 샘플은 없음 → 에러필드는 여전히 [가정].)
- **Codex 감지 = 검증됨 [실측]**: rollout `.type`={event_msg,response_item,session_meta,turn_context}; `event_msg.payload.type`={`task_started`,`task_complete`,`token_count`,`user_message`}; `token_count.payload.rate_limits` non-null 확인. → 완료=`task_complete`, rate-limit=`token_count.rate_limits` 확정.
- **CLI 보정**: **iTerm2 미설치 / Ghostty 설치 / tmux 설치**. → 보고서 1순위 "iTerm2 Python API" 경로는 **이 머신 불가**. CLI 자동주입 주력 = **`tmux send-keys`**, Ghostty는 세션 API 없어 osascript+창게이트로 격하.
- **OCR 보정**: `mac-ocr`/`ocrmac` 미설치, **tesseract만 설치**. Vision OCR 쓰려면 별도 설치(프리빌트 `mac-ocr` 권장). `cliclick` 미설치.

---

## 2. 단계별 돌파법 (검증된 사실 + 출처)

### 2-1. 감지·분류 (세션 로그)
- **Claude**: `~/.claude/projects/<proj>/<session_id>.jsonl` 의 `.error`(rate_limit/server_error…) · `.apiErrorStatus`(429/503) · `.isApiErrorMessage` · `.message.stop_reason`(`end_turn`=완료, `tool_use`=도구대기). **[가정]** — 이번 run에서 재검증 안 됨(기존 PLAN 전제).
- **Codex**: `~/.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl` 의 `task_complete` 이벤트, `token_count…rate_limits.rate_limit_reached_type`, `turn_context.realtime_active`. 전용 에러 이벤트는 없어 rate-limit 필드 + task_complete 부재 + idle로 추론. **[가정]** (출처: rollout 트레이스 리버스엔지니어링 블로그 2건)
- **Gemini**: 공식 session-management 문서가 존재하고 CLI가 **전체 대화 이력을 저장은 함**. 그러나 **상태 분류에 쓸 온디스크 포맷/스키마는 문서화돼 있지 않음**. "단일 session-*.json 통파일" 설도, "JSONL 전환" 설도 **둘 다 기각**(§7). → **리버스엔지니어링 필요**, 1차 폴백은 mtime-idle. **[검증]**
  - 출처: [gemini-cli session-management.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/session-management.md), [issue#15292](https://github.com/google-gemini/gemini-cli/issues/15292)
- **공통**: 파일 mtime idle은 1차 트리거로 유효하나 그 자체로는 약신호 — 내용 분류로 확정해야 함.

### 2-2. 창↔세션 매핑 / 타겟팅 (창게이트)
- **CG 창 열거**: `CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, …)` 로 `ownerPID` + `kCGWindowNumber`(CGWindowID) 별 온스크린 창 목록을 얻는다. **[검증]**
  - 출처: [chromium accessibility overview](https://chromium.googlesource.com/chromium/src/+/main/docs/accessibility/overview.md), [metamove/window.mm](https://github.com/jmgao/metamove/blob/master/src/window.mm)
- **AX↔CG 브릿지**: 특정 프로세스의 AXUIElement 창을 열거하고, **private `_AXUIElementGetWindow`** 로 각 AX 창의 CGWindowID를 얻어 CG 목록과 매칭 → "이 AX 창 = 이 화면 창" 확정. **[검증]** (단 private API, `weak_import` 가드 필요)
  - 출처: [metamove/window.mm](https://github.com/jmgao/metamove/blob/master/src/window.mm)
- **Electron AX 강제 활성화**: Electron/Chromium은 **기본적으로 AX 트리를 노출하지 않음**. 외부 클라이언트가 앱의 AXUIElement에 **`AXManualAccessibility=true`** 를 세팅하면 Chromium이 접근성 트리를 빌드하도록 강제할 수 있다. **[검증, 2-1표]**
  - 기각된 반대주장(§7): "AXManualAccessibility는 Electron 19~23서 전부 실패한다" **[기각]**, "VoiceOver 켜져야만 활성화된다" **[기각]** → 즉 **자동화 클라이언트가 강제 활성화 가능**.
  - 단 **버전 의존적** → 런타임 프로브(세팅 후 트리 빌드되는지 확인) 필요.
  - 출처: [electron accessibility tutorial](https://www.electronjs.org/docs/latest/tutorial/accessibility/), [chromium overview](https://chromium.googlesource.com/chromium/src/+/main/docs/accessibility/overview.md)
- **Codex 앱(AX창 0)**: 위 파이프라인이 적용되는지 **미확인**. CG 열거로 창 자체는 보이나, AX 창이 0이면 AX 브릿지·입력필드 포커싱이 막힘. AXManualAccessibility 강제 후 창이 잡히는지가 관건 — **미해결 과제**.

### 2-3. 화면 상태 확정 (OCR 게이트)
- Electron은 AX로 화면 텍스트를 못 읽으므로 **screencapture → OCR**로 에러배너/스피너/"생성중" 확정해 **생성 도중 오주입 차단**.
- **Apple Vision framework OCR이 tesseract보다 정확**하고 온디바이스. 래퍼 3종 모두 사용 가능, **per-result 바운딩박스** 제공(특정 영역의 배너/버튼 위치 판정 가능). **[검증]**
  - 출처: [ocrmac](https://github.com/straussmaximilian/ocrmac)(Vision 파이썬 래퍼), [macos-vision-ocr](https://github.com/bytefer/macos-vision-ocr)(CLI), [mac-ocr](https://github.com/privatenumber/mac-ocr)(npm 프리빌트 유니버설 바이너리), [Apple/Google OCR 비교](https://fritz.ai/comparing-apples-and-google-s-on-device-ocr-technologies/)
- **권장**: tesseract 폴백 유지하되, 가능하면 `mac-ocr`(프리빌트 바이너리, 설치 간단) 또는 Vision 헬퍼로 정확도 상향.

### 2-4. 키/텍스트 주입
- **유니코드 직접 키주입은 신뢰 불가**: `CGEventKeyboardSetUnicodeString` 기반 유니코드 주입은 **macOS 12(Monterey)부터 악화돼 조용히 실패**. "레이아웃 무관하게 안전하다"는 주장은 **기각**. **[검증]**
  - 출처: [Apple dev forum #706245](https://developer.apple.com/forums/thread/706245), [keyboardSetUnicodeString 문서](https://developer.apple.com/documentation/coregraphics/cgevent/1456028-keyboardsetunicodestring)
- **클립보드 붙여넣기가 신뢰 가능 경로**: 붙여넣기 시뮬레이션은 문자를 안정적으로 주입. **클립보드 백업 → set → 대상창 raise → Cmd+V → Enter → 클립보드 복원**. **[검증]** (= nonya 현재 설계와 일치, 옳음)
  - 단 **붙여넣기는 클립보드를 덮어씀** → 백업/복원 필수(현 설계 반영됨).
  - 출처: [macscripter 클립보드 저장/복원](https://www.macscripter.net/t/store-and-retrieve-pasteboard-clipboard/34680)
- **CLI는 별도(우월) 경로** — §2-5.

### 2-5. CLI 타겟 (가장 확실)
- **iTerm2 Python API**: `session.async_send_text(...)` 로 **session_id 지정해 특정 세션에 직접 텍스트 주입** — 창 포커스/매핑 불필요, 다중 pane·창에도 정확. **[검증]**
  - 출처: [iTerm2 targeted_input](https://iterm2.com/python-api/examples/targeted_input.html)
- **tmux**: `tmux send-keys -t <session>:<window>.<pane> '...' Enter` 로 특정 pane 주입. **[검증]** (단 send-keys 신뢰도·세션→pane 매핑 정확도는 이번에 깊게 검증 안 됨 — §6)
  - 출처: [tmux send-keys to pane](https://til.hashrocket.com/posts/ztxjgrqxhm-tmux-send-keys-to-pane), [tmux orchestration](https://primeline.cc/blog/tmux-orchestration)
- **함의**: CLI는 멀티페인이어도 **alert-only로 후퇴할 필요 없음** — iTerm2/tmux면 자동주입 가능. (Terminal.app·Ghostty는 세션 API가 없어 osascript+창게이트로 격하.)

### 2-6. 권한 모델 (프리플라이트 · graceful degrade)
- **Accessibility**: `AXIsProcessTrustedWithOptions` 로 프리플라이트(프롬프트 옵션 포함). 미보유 → 키/AX 동작 불가 → 알림만. **[검증]**
  - 출처: [AXIsProcessTrustedWithOptions](https://developer.apple.com/documentation/applicationservices/1459186-axisprocesstrustedwithoptions)
- **Screen Recording**: `CGPreflightScreenCaptureAccess` 로 프리플라이트, `CGRequestScreenCaptureAccess` 로 요청. 미보유 → OCR 생략 → (AX만으로 확정 불가하면) 알림만. **[검증]**
  - 출처: [CGPreflightScreenCaptureAccess](https://developer.apple.com/documentation/coregraphics/3656523-cgpreflightscreencaptureaccess)
- **Automation(AppleEvents)**: `AEDeterminePermissionToAutomateTarget` 로 타겟앱 자동화 권한 사전판정. 미보유 → osascript 주입 불가. **[검증]**
  - 출처: [mjtsai AEDeterminePermissionToAutomateTarget](https://mjtsai.com/blog/2018/08/31/aedeterminepermissiontoautomatetarget-added-but-aepocalyse-still-looms/)
- **degrade 사다리**: 권한 누락 시 해당 단계만 끄고 한 단계 낮은 신뢰도로 → 최종적으로 항상 **알림만**으로 안전 착지(오주입 0 불변식 유지).

---

## 3. 타겟 × 단계 매트릭스

| 단계 | Claude 앱 | Codex 앱 | Gemini 앱 | CLI |
|---|---|---|---|---|
| **감지(로그)** | jsonl 에러/stop_reason **[가정]** | rollout 이벤트+rate_limit **[가정]** | 스키마 미문서 → RE/ mtime **[검증]** | transcript + mtime |
| **분류** | 신뢰 높음(명시 에러) | 추론(전용 에러 이벤트 없음) | 약함(스키마 불명) | 엔진별 transcript 재사용 |
| **창게이트** | AX강제+CG/AX매핑 **[검증]** | **AX창 0 — 미해결** | AX강제+CG/AX매핑 **[검증]** | **불필요**(session_id 타겟) |
| **OCR확정** | Vision OCR **[검증]** | Vision OCR(창 못잡으면 무의미) | Vision OCR **[검증]** | 불필요(텍스트 직접) |
| **주입** | 클립보드 붙여넣기 **[검증]** | (창게이트 통과 시) 붙여넣기 | 클립보드 붙여넣기 **[검증]** | iTerm2 async_send_text / tmux send-keys **[검증]** |
| **검증** | mtime 증가 + 재OCR | 동일 | 동일 | 프롬프트 복귀/`<<DONE>>` |
| **도달 신뢰도** | 中~高 | **低(미해결)** | 中 | **高** |

---

## 4. 타겟별 'alert-only → auto-inject' 실행 경로

1. **CLI (최우선, 가장 확실)** — iTerm2면 Python API 세션 타겟 주입, tmux면 `send-keys -t pane`. 창 매핑·OCR 불필요. Terminal.app/Ghostty는 osascript+창게이트로 격하하거나 알림만.
2. **Claude 앱** — 컴파일드 헬퍼로 `AXManualAccessibility` 강제 → CG/AX 창 매핑(단일창이면 바로 확정) → Vision OCR로 "생성중 아님·에러배너" 확정 → 클립보드 붙여넣기. 다중창이면 매핑 확신 안 될 때 알림만.
3. **Gemini 앱** — Claude 앱과 동일 파이프라인. 단 감지단(로그 스키마 미문서)이 약점 → mtime-idle + OCR 화면판정 비중↑.
4. **Codex 앱 (미해결)** — `AXManualAccessibility` 강제 후 AX 창이 잡히는지부터 **실측 필요**. 잡히면 Claude와 동일 경로, 안 잡히면 CG 창 좌표 기반 클릭+붙여넣기(저신뢰)거나 알림만 유지.

---

## 5. nonya 코드 반영 포인트 (현 설계 대비 델타)

- **현 설계와 일치(유지)**: 클립보드 백업→붙여넣기→복원, 단일창 안전게이트, 권한 미보유 시 알림만, OCR로 생성중 차단.
- **추가/변경 권고**:
  1. **컴파일드 헬퍼 바이너리 신설** (Swift/Obj-C): `AXManualAccessibility` set, `_AXUIElementGetWindow` 기반 CG/AX 창 매핑, Vision OCR(바운딩박스). osascript로 불가한 부분 담당. ← Electron 자동주입의 전제.
  2. **OCR 엔진 업그레이드**: tesseract → Apple Vision(`mac-ocr` 프리빌트 바이너리) 우선, tesseract 폴백.
  3. **CLI 경로 분리**: iTerm2 감지 시 Python API 경로, tmux 감지 시 send-keys 경로를 별도 우선 처리(창게이트 건너뜀).
  4. **유니코드 키주입 금지 명문화**: `keyboardSetUnicodeString` 경로 쓰지 말 것(macOS12+ 실패). 붙여넣기 단일화.
  5. **Gemini 감지 RE 태스크**: gemini CLI/앱 온디스크 로그 위치·포맷 직접 조사(문서 부재 확정).

---

## 6. 미검증 / 미해결 (open questions)

**§1.5 로컬 실측으로 해소된 항목:**
- ~~Gemini 온디스크 스키마 불명~~ → **해소**: Antigravity CLI = SQLite `steps.status`+`error_details` (+`cli-*.log` HTTP 코드).
- ~~Claude·Codex 상태필드 포맷 미검증~~ → **구조 검증**: Claude `stop_reason`, Codex `task_complete`/`token_count.rate_limits` 실재 확인.

**여전히 미해결:**
- **Codex 앱 AX창 0 타겟팅** — AXManualAccessibility 강제 후 창 잡히는지 실측 안 됨. **최대 리스크.** (런타임 프로브 필요 — 앱 실행 + Accessibility 권한 상태에서 실측해야)
- **Antigravity GUI 앱/IDE 대화 저장 위치** — `~/.gemini/antigravity{,-ide}/conversations/`가 비어 있어 GUI 측 감지 경로 불명(CLI는 해결됨). 앱 사용 후 재조사 필요.
- **Claude/Codex 에러 레코드 실물** — 정상 세션만 있어 `.error`/`apiErrorStatus`/Codex 에러표현 실샘플 미수집. 에러 발생 세션에서 확인 필요.
- **tmux send-keys 신뢰도 + 세션→pane 매핑 정확도** — 깊게 검증 안 됨. (이 머신 CLI 주력 경로라 우선 검증 대상)
- **AXManualAccessibility 버전 의존성** — 런타임 프로브로 앱별(Claude/Codex/Antigravity 각 Electron 버전) 실측 필요.
- **private `_AXUIElementGetWindow`** — 비공개 API, OS 업데이트 시 깨질 수 있음. weak_import + 폴백 필수.

---

## 7. 적대검증서 기각된 주장 (믿지 말 것)

| 기각 주장 | 표결 | 함의 |
|---|---|---|
| gemini-cli가 단일 `session-*.json` 통파일로 매 메시지마다 전체 재기록 | 1-2 | 통파일 포맷 단정 금지 |
| gemini-cli JSONL 전환 제안이 'not planned'로 닫혀 JSONL은 출하 포맷 아님 | 0-3 | 포맷 미확정 |
| AXManualAccessibility가 Electron 19~23(Catalina)서 전부 실패 | 1-2 | **실패 단정 오류 — 동작 가능** |
| Electron이 속성을 광고 안 해 override 필요 | 0-3 | 불필요 |
| AX 트리 활성화가 VoiceOver 켜짐에 게이트됨 | 0-3 | **자동화 클라이언트가 강제 가능** |
| keyboardSetUnicodeString가 레이아웃 무관 안전주입 보장 | 1-2 | **불가 — 붙여넣기 써라** |

---

## 8. 출처 (각도별)

- **감지**: [Codex rollout 트레이스 RE](https://dev.to/milkoor/reverse-engineering-codex-cli-rollout-traces-3b9b) · [Codex 로그 진단](https://codex.danielvaughan.com/2026/05/21/codex-cli-log-files-debug-tracing-diagnostic-toolkit-troubleshooting/) · [gemini session-management](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/session-management.md) · [gemini issue#15292](https://github.com/google-gemini/gemini-cli/issues/15292)
- **창게이트**: [electron accessibility](https://www.electronjs.org/docs/latest/tutorial/accessibility/) · [electron#37465](https://github.com/electron/electron/issues/37465) · [electron#7206](https://github.com/electron/electron/issues/7206) · [metamove/window.mm](https://github.com/jmgao/metamove/blob/master/src/window.mm) · [chromium a11y overview](https://chromium.googlesource.com/chromium/src/+/main/docs/accessibility/overview.md)
- **OCR**: [ocrmac](https://github.com/straussmaximilian/ocrmac) · [mac-ocr-script](https://evanhahn.com/mac-ocr-script/) · [macos-vision-ocr](https://github.com/bytefer/macos-vision-ocr) · [mac-ocr](https://github.com/privatenumber/mac-ocr) · [Apple vs Google OCR](https://fritz.ai/comparing-apples-and-google-s-on-device-ocr-technologies/)
- **주입**: [keyboardSetUnicodeString](https://developer.apple.com/documentation/coregraphics/cgevent/1456028-keyboardsetunicodestring) · [유니코드 주입](https://isamert.net/2022/08/12/typing-unicode-characters-programmatically-on-linux-and-macos.html) · [클립보드 저장/복원](https://www.macscripter.net/t/store-and-retrieve-pasteboard-clipboard/34680) · [Apple dev#706245](https://developer.apple.com/forums/thread/706245)
- **CLI**: [tmux send-keys](https://til.hashrocket.com/posts/ztxjgrqxhm-tmux-send-keys-to-pane) · [tmux orchestration](https://primeline.cc/blog/tmux-orchestration) · [iTerm2 targeted_input](https://iterm2.com/python-api/examples/targeted_input.html)
- **권한**: [AXIsProcessTrustedWithOptions](https://developer.apple.com/documentation/applicationservices/1459186-axisprocesstrustedwithoptions) · [CGPreflightScreenCaptureAccess](https://developer.apple.com/documentation/coregraphics/3656523-cgpreflightscreencaptureaccess) · [AEDeterminePermissionToAutomateTarget](https://mjtsai.com/blog/2018/08/31/aedeterminepermissiontoautomatetarget-added-but-aepocalyse-still-looms/) · [Apple dev#732726](https://developer.apple.com/forums/thread/732726)

---

*검증 통계: 6각도 / 30소스 / 135주장 추출 / 25주장 적대검증 / 19 confirmed · 6 killed. 합성 후 핵심결론 3개 + 미해결 6개.*
