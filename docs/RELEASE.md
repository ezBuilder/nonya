# nonya 배포 가이드 (maintainer)

클코·코덱스 사용자에게 **공증된 .app/DMG**로 배포하는 전 과정. nonya = 크로스플랫폼 Python 코어 + macOS 네이티브 펫 셸([macos/](../macos)). 코어는 순수 stdlib라 의존성 0.

## 빌드 파이프라인

```bash
# 1) Python 코어를 단독 바이너리로 번들 (시스템 python3 불요)
bash packaging/build-core.sh          # -> build/dist/nonya

# 2) NonyaPet.app 조립 (Swift 셸 + 임베드 코어)
bash packaging/build-app.sh           # -> build/Nonya.app  (ad-hoc 서명, 미공증)

# 3) 전체 검증
bash tests/e2e.sh --live              # 단위 + 번들코어 주입 + 앱번들 + 라이브 GUI
```

## 현재 공개 배포

- 최신 태그: `v0.2.3`
- macOS DMG: [GitHub Releases](https://github.com/ezBuilder/nonya/releases/tag/v0.2.3)
- 카드뉴스 PNG: [../assets/marketing/cardnews/](../assets/marketing/cardnews/)

## 서명·공증·DMG (1회 사전준비 후 1커맨드)

**사전준비 (네 Apple 계정 — 자동화 불가, 기존 $99/년 멤버십으로 커버):**
1. **Developer ID Application** 인증서 생성: Xcode > Settings > Accounts > Manage Certificates > `+` > "Developer ID Application". (현재 키체인엔 "Apple Development"만 있어 로컬 테스트만 가능 — 배포 공증엔 Developer ID 필수.)
2. 공증 자격증명 저장(앱 암호):
   ```bash
   xcrun notarytool store-credentials nonya-notary \
     --apple-id you@example.com --team-id TEAMID --password <app-specific-password>
   ```

**배포 빌드:**
```bash
DEV_ID="Developer ID Application: Your Name (TEAMID)" \
NOTARY_PROFILE="nonya-notary" \
bash packaging/sign-notarize.sh       # -> build/nonya-<ver>.dmg (서명·공증·staple 완료)
```

스크립트는 inside-out 서명(임베드 코어 → 앱) + hardened runtime + 엔타이틀먼트([macos/NonyaPet.entitlements](../macos/NonyaPet.entitlements): apple-events, disable-library-validation)로 PyInstaller 코어 공증 통과를 보장.

## end-user 설치 (배포물 받는 사람)

1. `nonya-<ver>.dmg` 열고 `nonya.app`을 Applications로 드래그.
2. nonya 실행 → 메뉴바에 🦆 등장.
3. 첫 실행 시 **손쉬운 사용(Accessibility)** 허용 안내 → 설정에서 nonya 체크.
   (OCR 화면확정까지 원하면 **화면 기록**도 허용. 미허용이어도 감지·알림은 동작.)
4. 메뉴바 🦆 → **Claude Code / Codex / 둘 다 감시** 클릭.
   - 기본은 **안전 모드(on-error)** — 진짜 에러·과부하·멈춤일 때만 재시도, 정상 세션은 안 건드림.
   - **자율 모드(밤샘)** 체크 시 멈추면 `<<DONE>>`까지 계속(무인 야간용).
5. 멈춘 게 잡히면 펫이 혼내며 그 창에 명령을 다시 넣어 이어가게 함. 몇 회 무반응이면 텔레그램/슬랙으로 알림(환경변수 설정 시).

## 권한 모델 (왜 필요한가)
- **손쉬운 사용**: 멈춘 창에 붙여넣기·Enter 주입. 없으면 알림만.
- **화면 기록**: OCR로 "생성 중"인지 확정해 오주입 방지(선택).
- **자동화(System Events)**: 창 raise·포커스. Info.plist에 사유 명시.
- 모든 처리는 **로컬**. 외부 전송은 사용자가 켠 텔레그램/슬랙 알림뿐.

## 안전 불변식
타겟 창을 확신 못 하거나 권한 미충족이면 **절대 키 0, 알림만**. 라이브 검증으로 다중창→키0 실증됨([tests/e2e.sh](../tests/e2e.sh)).

## Windows 패키지

코어(Python)는 크로스플랫폼이고, Windows CLI 배포물은 Windows에서 PyInstaller로 만든다. macOS에서 `.exe`를 크로스컴파일하지 않는다.

로컬 Windows/VM:

```powershell
.\packaging\build-windows.ps1
```

출력:

```text
build\nonya-<version>-windows-x64.zip
```

GitHub Actions:

- `.github/workflows/windows-package.yml`
- `main`/`develop` push 또는 수동 `workflow_dispatch`
- artifact: `nonya-windows-x64`

서명:

```powershell
.\packaging\build-windows.ps1 `
  -SignTool "C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe" `
  -SignSubject "Your Code Signing Certificate Subject"
```

주의:

- ZIP 배포는 가능하지만, 일반 사용자 경고를 줄이려면 Authenticode 코드서명 인증서가 필요하다.
- Windows GUI 앱 주입은 Win32 백엔드가 있으나 실기기 검증 전에는 보수적으로 안내한다.
- WSL/tmux CLI 경로와 네이티브 Windows 앱 경로는 별도 proof로 분리한다.
- 자세한 배경: [RESEARCH-windows-auto-inject-2026-06-19.md](RESEARCH-windows-auto-inject-2026-06-19.md).

## 자율 모드와 입력대기

`--mode auto`는 입력대기에서 그대로 잠들지 않는다. 안전한 자동화는 세 갈래다.

1. Claude Code PreToolUse hook: `pytest`, `git status`, 파일 읽기처럼 낮은 위험의 되돌릴 수 있는 작업만 자동 승인한다.
2. transcript가 평문 질문으로 끝난 경우: `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/README.*.md` 안에서 답이 명확히 발견될 때만 그 줄을 답한다.
3. 문서에 답이 없으면 "가장 안전하고 되돌릴 수 있는 로컬 기본값으로 계속 진행"이라는 자율모드 기본 응답을 주입한다.

destructive/secret/billing/deploy/install/network/privilege/production/publish/release 프롬프트는 승인하지 않는다. 대신 "그 위험 작업은 하지 말고 로컬 검증·dry-run·요약으로 진행"이라고 답해 사용자가 자는 동안에도 세션이 입력대기에서 놀지 않게 한다.
