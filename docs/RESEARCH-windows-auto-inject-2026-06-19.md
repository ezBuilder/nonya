# 연구(Windows): 4개 타겟 자동 인-윈도우 주입 (2026-06-19)

> macOS 연구([RESEARCH-auto-inject-2026-06-19.md](RESEARCH-auto-inject-2026-06-19.md))의 **Windows 대칭판**. 목표는 크로스플랫폼(Win+Mac) 단일 아키텍처.
> 출처: deep-research run `wf_6f522310-ff3` (5각도 → 21소스 → 96주장 → 25주장 3표 적대검증 → 23 confirmed / 2 killed).
> 검증 표기: **[검증]**=3표 통과(대부분 Microsoft Learn 1차), **[중간]**=문서 침묵으로 PoC 필요, **[미검증]**=이번 라운드 1차 출처 미확인.

---

## 1. 한 줄 결론

Windows 인-윈도우 주입은 **기술적으로 가능하나, 핵심 게이트가 macOS의 권한 동의(TCC)에서 Windows의 무결성 레벨(IL)/UIPI로 바뀐다.**

- **감지 코어는 OS 공유 유망** — Claude(JSONL)·Codex(JSONL)·Antigravity(SQLite) 로그 포맷은 OS 독립적. Windows 경로(`%USERPROFILE%` vs `%APPDATA%`)와 바이트 호환성만 실측하면 분류 로직 재사용. **[미검증 — 경로 확인 필요]**
- **창 매핑은 깔끔** — `EnumWindows`+`GetWindowThreadProcessId`로 HWND→PID. 타겟들은 고전 Win32 데스크톱 앱이라 정상 열거. **[검증]**
- **Electron 트리는 Mac과 동일하게 기본 숨김** → `--force-renderer-accessibility` 또는 `app.setAccessibilitySupportEnabled(true)`로 강제. Windows 1903+가 IA2→UIA 브리지. **[검증]**
- **주입의 결정적 벽 = UIPI/IL** — `SendInput`은 동등·하위 IL 창에만 주입, **차단 시 조용히 실패**(GetLastError·반환값으로 식별 불가). 에이전트 앱이 관리자(High IL)로 떠 있으면 nonya가 medium IL이면 주입 불가. **[검증]**
- **상위 IL 주입엔 UIAccess 필요** — 서명(Trusted Root CA)+`%ProgramFiles%` 설치+매니페스트 UIAccess 플래그+관리자 실행. 충족 시 SetForegroundWindow·임의창 SendInput·저수준 훅 전부 획득. 비용 큼. **[검증]**

---

## 2. 단계별 (검증된 사실 + 출처)

### 2-1. 감지·분류
- 로그 포맷이 OS 독립(JSONL/SQLite)이라 **분류 코어 재사용 가능성 높음**. 단 Windows 실제 경로(`%USERPROFILE%\.claude` / `.codex` / `.gemini` vs `%APPDATA%`/`%LOCALAPPDATA%`)와 포맷 동일성은 **미검증** → 실측 필요.
- 파일 변경 감시: `ReadDirectoryChangesW`(Win) ↔ FSEvents/kqueue(Mac). mtime-idle은 동일.

### 2-2. 창↔세션 매핑 / 타겟팅
- `EnumWindows` → 모든 top-level 창 콜백 열거; `GetWindowThreadProcessId(HWND, &pid)` → HWND→PID. **[검증]** (Win8+는 데스크톱 앱만 열거 — Electron/터미널 OK, UWP만 제외)
- Electron/Chromium UIA: Chromium은 Windows에서 MSAA/IAccessible2/UIA 노출, OS(1903+)가 IA2↔UIA 매핑. Chrome 126+ 네이티브 UIA 단계출시, 138+ 기본활성. **[검증]**
  - ⚠️ **`IUIAutomation::ElementFromHandle`로 HWND→UIA 직접 획득 경로는 기각(1-2)** → OS IA2→UIA 브리지에 의존, 직접경로 신뢰성은 별도 실측.
  - 출처: [chromium a11y overview](https://chromium.googlesource.com/chromium/src/+/lkgr/docs/accessibility/overview.md), [chrome windows-uia](https://developer.chrome.com/blog/windows-uia-support), [a11y insights FAQ](https://accessibilityinsights.io/docs/windows/reference/faq/)
- **Electron 트리 강제 활성화**: Chrome a11y는 "off by default, on-demand"(보조기술 감지 시만). → `--force-renderer-accessibility=[basic|form-controls|complete]`(런타임 플래그) 또는 `app.setAccessibilitySupportEnabled(true)`(앱 코드/재시작). **[검증]** = macOS의 `AXManualAccessibility`와 동형 문제·해법.
  - 출처: [electron accessibility](https://www.electronjs.org/docs/latest/tutorial/accessibility/)

### 2-3. OCR — **[미검증]**
- 후보: `Windows.Media.Ocr`(WinRT 온디바이스) ↔ macOS Vision. 캡처: `PrintWindow`/`BitBlt`/`Windows.Graphics.Capture`. 이번 라운드 1차 출처 미검증 → 별도 확인 필요. (Mac Vision과 대칭 구조일 것으로 추정.)

### 2-4. 키/텍스트 주입
- **유니코드 안전**: `SendInput` `INPUT_KEYBOARD`+`KEYEVENTF_UNICODE`(wVk=0, wScan=유니코드) → 레이아웃 무관, **한국어 안전**. 단 **포그라운드 앱으로 전달** → `SetForegroundWindow` 필요. **[검증]**
  - 출처: [INPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input), [KEYBDINPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput), [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
- **UIPI 차단은 조용함**: 대상이 상위 IL이면 SendInput이 실패하는데 **반환값/GetLastError로 식별 불가** → 사후 검증(mtime 증가)으로만 성공 판정 가능. **[검증]**
- **PostMessage/WM_CHAR focus-less는 비신뢰**: Chromium 입력 파이프라인이 합성입력을 명시 안 함; PostMessage는 "prank calling"처럼 불안정, WM_CHAR 미생성 사례. SendInput은 실제입력처럼 처리되나 포그라운드 필요. **[중간 — PoC 필요]**
  - 함의: Electron 주입은 **SendInput+포그라운드** 또는 **UIA ValuePattern.SetValue/TextPattern** 또는 **클립보드 붙여넣기(Ctrl+V)** 중 실측 우위 경로 선택.
- **ChangeWindowMessageFilterEx/MSGFLT_ALLOW**: UIPI 메시지 필터 완화 가능하나 **대상 창이 설정하는 것** → nonya가 외부 앱에 적용 불가(무용). **[검증, 단 nonya엔 부적용]**

### 2-5. 터미널 주입 (CLI) — 대부분 **[미검증]**
- 후보: `WriteConsoleInput`(콘솔 입력버퍼 직접) vs `SendInput`, ConPTY, **WSL 내 tmux `send-keys -t pane`**(Mac과 동일 가능성), WSL↔Windows 창 경계. 이번 라운드 검증 클레임에 없음 → 실측 필요.
  - 출처(참고): [WriteConsoleInput](https://learn.microsoft.com/en-us/windows/console/writeconsoleinput), [terminal#6309](https://github.com/microsoft/terminal/pull/6309), [WSL interop](https://wsl.dev/technical-documentation/interop/)

### 2-6. 권한·신뢰 모델 (UIPI/UIAccess)
- **게이트 = IL/UIPI**(TCC 동의 아님). 보조기술은 medium IL로 실행, 상위 IL 프로세스 접근하려면 경계 통과 필요. UIPI: 하위→상위 메시지/훅 차단, 상위→하위 허용, 동등끼리 무영향. **[검증]**
  - 출처: [UIA security overview](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-securityoverview), [UIPI(jj852244)](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/jj852244(v=ws.11))
- **UIAccess 획득 조건(전부 충족 필수)**: ①Trusted Root CA 서명 ②`%ProgramFiles%`/`%WinDir%\system32` 등 보안위치 설치 ③매니페스트 UIAccess=true + 관리자 사용자 실행. → 충족 시 **SetForegroundWindow·임의창 SendInput·저수준 훅·AttachThreadInput** 전부 획득(포커스+주입 동시 해결). 미충족 시 동등·하위 IL만. **[검증]**
- **graceful degrade**: nonya가 medium IL & 대상도 medium이면 주입 OK(흔한 경우). 대상이 관리자(High)면 UIAccess 없으면 **알림만**. (= 오주입 0 불변식 유지)
- ⚠️ Project Zero(2026) UIAccess→High 우회 9건은 **전부 패치** → 익스플로잇 의존 금지.

---

## 3. 타겟 × 단계 매트릭스 (Windows)

| 단계 | Claude 앱 | Codex 앱 | Antigravity 앱 | CLI |
|---|---|---|---|---|
| **감지** | JSONL(경로 미검증) | rollout JSONL(경로 미검증) | SQLite(경로 미검증) | transcript+mtime |
| **창게이트** | EnumWindows+UIA(강제활성) **[검증]** | **Win 가시성 미검증** | EnumWindows+UIA(강제활성) | EnumWindows / WSL tmux |
| **OCR** | Windows.Media.Ocr **[미검증]** | 동일 | 동일 | 불필요 |
| **주입** | SendInput+FG / 붙여넣기 **[중간]** | (창 잡히면) 동일 | 동일 | WriteConsoleInput/tmux **[미검증]** |
| **권한게이트** | medium 동일IL→OK / 관리자→UIAccess | 동일 | 동일 | WSL 경계 |
| **신뢰도** | 中(IL 동일 시) | 低(미검증) | 中 | 中(미검증) |

---

## 4. 미검증 / 미해결 (Windows open questions)

1. **세션 로그 실제 경로·포맷 동일성** — `%USERPROFILE%` vs `%APPDATA%`, JSONL/SQLite 바이트 호환? (감지 코어 공유의 전제) — Windows 머신 실측 필요.
2. **Codex 앱 Windows 가시성** — EnumWindows/UIA에 잡히는지(Mac에서 AX창0 문제 재현?).
3. **WSL tmux send-keys + WSL↔Windows 창 제어** 실제 동작.
4. **focus-less 주입 최적 경로** — SendInput+FG vs UIA ValuePattern vs 클립보드 붙여넣기, Electron 입력필드 실측.
5. **OCR 스택**(Windows.Media.Ocr + Windows.Graphics.Capture) 충분성.

---

## 5. 기각된 주장 (믿지 말 것)

| 기각 | 표결 | 함의 |
|---|---|---|
| `ElementFromHandle`로 HWND→UIA 요소 직접 획득 | 1-2 | 직접경로 의존 금지, OS 브리지 경유 |
| Chromium은 IA2로만 제공하고 Windows가 UIA로 변환 | 0-3 | 단정 금지(네이티브 UIA 출시 중) |

---

*검증 통계: 5각도 / 21소스 / 96주장 추출 / 25 적대검증 / 23 confirmed · 2 killed.*
