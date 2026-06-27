# nonya 배포 가이드 (maintainer)

클코·코덱스 사용자에게 **공증된 .app/DMG**로 배포하는 전 과정. nonya = 크로스플랫폼 Python 코어 + macOS 네이티브 펫 셸([macos/](../macos)). 코어는 순수 stdlib라 의존성 0.

## 빌드 파이프라인

```bash
# 1) Python 코어를 단독 바이너리로 번들 (시스템 python3 불요)
bash packaging/build-core.sh          # -> build/dist/nonya

# 2) NonyaPet.app 조립 (Swift 셸 + 임베드 코어)
bash packaging/build-app.sh           # -> build/NonyaPet.app  (미서명)

# 3) 전체 검증
bash tests/e2e.sh --live              # 단위 + 번들코어 주입 + 앱번들 + 라이브 GUI
```

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

## 윈도우 (후순위)
코어(Python)는 크로스플랫폼. Windows는 같은 코어 + Win32/UIA 백엔드 + 별도 네이티브 셸 예정([docs/RESEARCH-windows-auto-inject-2026-06-19.md](RESEARCH-windows-auto-inject-2026-06-19.md)).
