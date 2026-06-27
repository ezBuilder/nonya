# nonya 크로스플랫폼 아키텍처 (Win + Mac) — 2026-06-19

> 근거: [Mac 연구](RESEARCH-auto-inject-2026-06-19.md) + [Windows 연구](RESEARCH-windows-auto-inject-2026-06-19.md) + 로컬 실측.
> 요구사항: nonya를 **Windows·macOS 양 플랫폼**에서 Claude/Codex/Antigravity 앱 + CLI 4타겟 자동 인-윈도우 주입(decision `dec-6dc72947`).

---

## 1. 핵심 통찰 — 두 OS가 같은 구조다

| 관심사 | macOS | Windows | 결론 |
|---|---|---|---|
| **감지(로그)** | `~/.claude` JSONL · `~/.codex` JSONL · `~/.gemini/antigravity-cli` SQLite | 경로만 `%USERPROFILE%`/`%APPDATA%`로 이동, 포맷 동일 추정 | **OS 공유 코어** |
| **Electron 트리** | 기본 숨김 → `AXManualAccessibility` 강제 | 기본 숨김 → `--force-renderer-accessibility` 강제 | 동형 문제·동형 해법 |
| **창 매핑** | CGWindowList + `_AXUIElementGetWindow`(private) | `EnumWindows`+`GetWindowThreadProcessId` | OS별 백엔드 |
| **네이티브 키주입** | 유니코드 직접주입 불가(macOS12+) → 붙여넣기 | `SendInput`+UNICODE 가능하나 포커스 필요, focus-less 비신뢰 → 붙여넣기/UIA | OS별 백엔드, 붙여넣기 공통 |
| **OCR 확정** | Apple Vision | Windows.Media.Ocr | OS별 백엔드 |
| **권한/신뢰 게이트** | TCC(Accessibility·Screen Recording·Automation) | IL/UIPI/UIAccess | OS별, 둘 다 미충족→알림만 |
| **CLI(최고신뢰)** | tmux / iTerm2 API | WSL tmux / ConPTY | 거의 공유(tmux) |

→ **"감지·분류·정책·오케스트레이션은 OS 공유 코어, 창게이트·OCR·주입은 OS별 백엔드"** 가 자연스러운 경계. (양쪽 연구가 독립적으로 같은 결론.)

---

## 2. 결정적 제약: bash로는 양쪽 다 못 한다

- macOS 자동주입: AX 강제활성화·`_AXUIElementGetWindow` 창매핑·Vision OCR → **순수 osascript 불가, 컴파일드 헬퍼 필요**.
- Windows: `EnumWindows`/`SendInput`/UIA/`Windows.Media.Ocr` → **bash 네이티브 호출 불가**.
- ∴ **코어를 bash로 유지하면 OS별 헬퍼를 따로 짜고 bash가 조율**하거나, **코어 자체를 네이티브 API 바인딩 가능한 언어로 재작성**해야 한다.

---

## 3. 권장 아키텍처

```
nonya/
  core/                    # OS 공유 — 네이티브 API 의존 없음
    detect/
      claude.py            # JSONL: stop_reason/error/apiErrorStatus
      codex.py             # rollout JSONL: task_complete/token_count.rate_limits
      antigravity.py       # SQLite: steps.status/error_details (+cli-*.log)
    classify.py            # STATE = ERROR|RATE_LIMIT|TOOL_PENDING|COMPLETED|IDLE_WAIT
    policy.py              # mode(on-error/auto), 안전 불변식, sentinel <<DONE>>
    loop.py                # poll→classify→gate→confirm→inject→verify
    notify.py              # 알림(크로스플랫폼)
  backends/
    base.py                # WindowGate/OCR/Injector 인터페이스(추상)
    macos/                 # pyobjc + Swift 헬퍼: AX강제, CG/AX매핑, Vision, 붙여넣기
    windows/               # pywin32/uiautomation/comtypes: EnumWindows, UIA강제, SendInput/붙여넣기, WinRT OCR
  cli/                     # tmux send-keys (양 OS 공유), iTerm2(Mac)/ConPTY(Win) 특화
```

**안전 불변식(양 OS 공통)**: 타겟 확신 못 하거나 권한/IL 미충족 → **절대 주입 안 함, 알림만**. 오주입 > 미복구.

---

## 4. 언어 선택 — Python 권장 (단, 아키텍처 추론·1차검증 아님)

| 후보 | Mac 네이티브 | Win 네이티브 | 단일언어 양OS | 비고 |
|---|---|---|---|---|
| **Python** | pyobjc(AX/CG/Vision/AppleScript) | pywin32·uiautomation·comtypes(Win32/UIA/SendInput/WinRT) | ✅ 가장 강함 | **권장** |
| Go | CGo+Obj-C 수동 | syscall/win32 바인딩 | △ UI오토메이션 약함 | |
| .NET | Avalonia/약한 AX | 최강(UIA/Win32 네이티브) | △ Mac측 약함 | Win 단독이면 최적 |
| Node | native addon | native addon | △ 무거움 | |
| bash+osascript(현재) | 헬퍼 필요 | **불가** | ✗ | Win 네이티브 불가 |

→ **Python 단일언어 + OS별 백엔드 모듈 + (Mac은 작은 Swift 헬퍼 보조)** 가 가장 깔끔. 단 언어비교는 본 연구에서 1차 출처 검증된 사실이 아니라 API 바인딩 가용성 기반 추론이므로, 착수 전 PoC로 확정 권장.

---

## 5. 신뢰도 상한 (타겟 × OS, 현실적)

| 타겟 | macOS | Windows |
|---|---|---|
| **CLI(tmux)** | 高 | 中~高(WSL 경계 실측 필요) |
| **Claude 앱** | 中~高(단일창) | 中(동일 IL 시) |
| **Antigravity 앱** | 中 | 中 |
| **Codex 앱** | 低(AX창0 미해결) | 低(Win 가시성 미검증) |
| 관리자권한 에이전트 | — | 低(UIAccess 없으면 알림만) |

---

## 6. 착수 전 실측 체크리스트 (PoC로 닫을 미해결)

- [ ] **Mac**: Codex 앱 AXManualAccessibility 프로브 / Antigravity GUI 대화 저장위치 / tmux→pane 매핑
- [ ] **Win**: 세션 로그 실제 경로·포맷 동일성 / Codex 앱 EnumWindows·UIA 가시성 / WSL tmux send-keys + Windows 창 제어 / focus-less 주입 최적경로(SendInput+FG vs UIA vs 붙여넣기) / Windows.Media.Ocr 충분성
- [ ] **공통**: Python pyobjc·pywin32 PoC로 언어 확정

---

## 7. 단계적 실행 경로 (권장 순서)

1. **PoC**: Python으로 (a) Mac 감지코어 3종 + tmux 주입, (b) Win 감지코어 동일 + EnumWindows/SendInput 1샷. 미해결 체크리스트 동시 소거.
2. **코어 추출**: detect/classify/policy/loop를 OS 무관 Python으로.
3. **Mac 백엔드**: pyobjc + Swift 헬퍼(AX/CG/Vision/붙여넣기). 현 bash 로직을 포팅.
4. **Win 백엔드**: pywin32/uiautomation(EnumWindows/UIA강제/SendInput·붙여넣기/WinRT OCR).
5. **권한/IL 게이트 + graceful degrade** 양 OS.
6. **검증 매트릭스**(PLAN.md 7단계 ×2 OS) + install 심(launchd / Windows 등록).

> ⚠️ 이는 현 bash 스켈레톤(~500줄, macOS 전용)을 **Python으로 재작성**함을 의미 — 큰 결정. §7 PoC부터 시작해 위험을 먼저 닫는 것을 권장.
