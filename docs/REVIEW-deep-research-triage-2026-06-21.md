# nonya 코드 직접검증 + 외부 딥리서치 2건 대조 (2026-06-21)

두 외부 보고서(`노냐-deep-research-report`, `노냐 앱 심층 분석`)는 **둘 다 레포를 클론하지 않고
사용자 요약 기반**으로 작성됐다(보고서 1은 그렇게 명시). 이 문서는 **레포 코드를 직접 읽고
라이브 테스트로 검증**한 결과 + 외부 지적의 사실판정 + 이번에 실제로 고친 것이다.

---

## 0. 가장 중요한 발견 — 외부 보고서 둘 다 못 잡은 출하-치명 버그

**증상:** 사용자가 "아직 효과를 못 봤다." → 단순 체감이 아니라 **실제 버그**였다.

**근본원인:** tmux pane 탐색(`pane_for_cwd`, `find_pane`)이 `-F "#{pane_id}\t#{...}"`의 **탭 구분자**에
의존했는데, **PyInstaller로 동결된 출하 바이너리에서 이 `\t`가 변질**돼 tmux 출력이 탭으로 안 나옴
→ `split("\t")`가 아무것도 못 찾음 → **pane 탐색이 조용히 None 반환** → 어떤 tmux 세션도 타겟 불가
→ **출하된 앱에서 자동 복구가 한 번도 동작하지 않았다.**

- 소스(`python3 -m nonya`)에선 통과 → 기존 e2e가 `--tmux <pane>` 명시 타겟만 써서 **탐색 경로를 안 탐** → 못 잡힘.
- 라이브 증명: 번들 `--selftest`가 `pane_for_cwd=None`(소스는 `%121` 매칭).
- **수정:** 구분자를 공백+`split(None)`으로 교체(pane_id/pid는 공백 없음, path는 나머지로 보존).
  번들 `--selftest` 이제 PASS. e2e에 **번들 end-to-end 복구 가드** 추가.

이건 코드를 안 보면 절대 안 나오는 종류라, 외부 보고서가 못 잡은 게 당연하다. 그러나 **사용자가 효과를
못 본 1순위 원인**이었다.

---

## 1. 코어가 실제로 동작하는가 — 라이브로 증명함

throwaway tmux 세션 + 가짜 stuck transcript로 **전체 루프**를 돌렸다:
감지(`classify4=stuck`) → cwd로 tmux pane 매칭 → 주입 → **세션이 마커 수신**. 소스·번들 모두 PASS.
→ `nonya --selftest` (또는 메뉴 "복구 자가진단")로 **누구나 직접 확인 가능**하게 만듦.

---

## 2. "효과를 못 본" 진짜 이유 2가지 (둘 다 해결)

1. **위 \t 버그** — 출하 앱이 tmux 타겟을 못 찾음 → 고침.
2. **사용자의 실제 세션이 tmux가 아님** — 진단 결과 감시 중 5개 claude 세션 전부 tmux pane 없음(GUI/raw 터미널).
   nonya는 설계상 tmux pane에만 안전 주입 → 주입할 대상이 0개 → "아무 일도 안 일어남."
   - 해결: 메뉴 세션 목록에 **"💡 N개는 tmux가 아니라 알림만 — 'tmux에서 시작'으로 자동복구"** 힌트 추가(정직).
   - `--launch claude|codex` / 메뉴 "tmux에서 시작"으로 띄우면 복구 가능. (외부 보고서 둘 다 "tmux-first" 권고와 일치.)

추가로 발견·수정한 **실버그**: claude cwd 디코더가 `dir.replace("-","/")`라 **경로에 `-`가 있으면 틀림**
(`code-brain`→`code/brain`→미해결). transcript 레코드의 실제 `cwd` 필드를 읽도록 수정(무손실). →
`brain` 세션 cwd가 이제 정확히 해결(이전 미해결) → tmux면 복구 가능.

---

## 3. 외부 보고서 지적 사실판정 (코드 대조)

### 정확 / 이미 반영됨
- **tmux-first가 가장 안전** ✅ 정확. 코드도 그렇게 설계(`--launch`, AX-split 기본 비활성). 메뉴/문구도 그 방향으로 강화 중.
- **"타겟 확신 못 하면 키 안 보냄" 불변식이 핵심자산** ✅ 코드에 구현(단일창 게이트, frontmost-only paste, 모호하면 알림만).
- **CGEvent.postToPid가 활성 split로 라우팅 → raw split 위험** ✅ 정확. `inject_terminal_split` 기본 비활성, 전달검증 가드 존재.
- **idle 시간만으론 추론지연 vs 방치 구분 불가** ✅ 정확. 그래서 "최근 출력 중"만 정직신호로 사용.
- **SHA-256 루프 지문이 견고** ✅ 구현돼 있음(`supervise.loop_fingerprint`, 휘발성키 정규화).
- **"4-state인데 값 5개" 네이밍 불일치** ✅ 정확(보고서1·2 공통 지적). `WORKING` 포함 5상태로 docstring/주석 정정함.

### 부분적으로만 맞음 / 과장
- **"disable-library-validation → dylib 하이재킹 + TCC 상속"** (보고서2): entitlement 사용은 사실(PyInstaller 필수).
  단, 최악 시나리오의 전제인 **`allow-dyld-environment-variables`는 우리 entitlements에 없다**(apple-events +
  disable-library-validation 둘뿐) → DYLD_INSERT 주입 경로는 막혀 있음. 위험은 보고서 서술보다 좁다.
  그래도 장기적으로 **민감권한(주입/캡처)을 별도 helper로 분리**하는 방향은 타당 → 백로그.
- **"클립보드 경유 붙여넣기 누수"** (보고서1): macOS 경로는 백업→붙여넣기→**복원**까지 함. 위험 0은 아니나
  보고서가 암시하는 만큼 방치돼 있진 않음. 복원검증/secure-mode는 합리적 개선 → 백로그.
- **OCR/Vision**: 현재 OCR은 **보조 확정**일 뿐 1차 신호가 아님(1차는 transcript). 보고서의 "OCR만 믿으면 위험"은
  맞지만 nonya는 이미 transcript-우선. Vision/ScreenCaptureKit 전환은 성능개선(필수 아님).

### 실측 필요 / 미검증 주장
- **"PyInstaller one-file 시작 느림 → helper 분리"**: 일리 있음. 다만 메뉴바앱이 코어를 spawn하는 현 구조에서
  체감 병목은 측정 안 됨. **shadow-mode 지표 계측 먼저**(보고서1 권고)가 더 우선.
- **"Windows UIA로 전환"**: 현재 Windows는 ctypes 구현(경계 미실측). macOS가 1차 플랫폼이라 후순위.
- **FSEvents / SQLite WAL**: 둘 다 합리적이나 현재 규모(수~수십 세션, 폴 15s)에선 체감 병목 미검증. 측정 후 도입.

---

## 4. 이번 세션에서 실제로 고친 것 (검증 포함)

| 항목 | 종류 | 검증 |
|---|---|---|
| 번들 `\t` 구분자 → pane 탐색 실패 (출하-치명) | **버그** | 번들 `--selftest` PASS, e2e 가드 추가 |
| claude cwd: dir-decode(손실) → transcript `cwd` 읽기(무손실) | **버그** | `brain` 등 dash 경로 해결, 단위테스트 |
| `nonya --selftest` + 메뉴 "복구 자가진단" | 기능(신뢰) | 소스·번들 PASS |
| 메뉴: tmux 아닌 세션 "알림만" 힌트 | UX(정직) | reach 필드 + 푸터 |
| "4-state"→5상태 네이밍 정정 | 문서정합 | docstring |

전부: 단위 25/25, e2e ALL GREEN, Developer ID 서명 유지.

---

## 5. 권장 우선순위 (두 보고서 + 코드검증 종합)

1. **(완료)** 출하 경로 복구 동작 보장 + 자가진단으로 가시화 + tmux 아님 정직표기.
2. **shadow-mode 계측**(보고서1 핵심): 오주입률/미복구율/탐지~복구 시간을 ledger로 수치화. → 신뢰의 근거.
3. **tmux-first 온보딩**: 첫 실행 시 "tmux에서 시작" 유도(가장 안전 경로를 기본 동선으로).
4. 민감권한 helper 분리(보안 하드닝) / 클립보드 secure-mode → 배포 확대 시.
5. ScreenCaptureKit·Vision·FSEvents·WAL → **측정으로 병목 확인 후** 선별 도입(미리 하지 말 것).

**결론:** 코어는 실제로 동작한다(라이브 증명). 사용자가 효과를 못 본 건 (a) 출하 바이너리의 `\t` 버그와
(b) 세션이 tmux 밖이라 주입 대상이 없던 것 — 둘 다 해결. 외부 보고서의 전략 방향(tmux-first, 신뢰지표,
보수적 개입)은 옳고, 코드는 이미 그 철학을 구현 중이었다. 다음 한 수는 **shadow-mode 지표 계측**이다.
