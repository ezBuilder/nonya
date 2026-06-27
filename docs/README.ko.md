# nonya (노냐?) - 한국어 소개

[README](../README.md) | [English](README.en.md) | [日本語](README.ja.md) | [简体中文](README.zh-Hans.md)

**노냐?**는 Claude, Codex, Antigravity 같은 AI 작업 세션이 멈췄는지 감시하고, 안전한 경우 같은 창이나 같은 tmux pane에서 이어가기 신호를 보내는 오픈소스 세션 자동복구 도구입니다.

다운로드: [v0.2.3 릴리즈](https://github.com/ezBuilder/nonya/releases/tag/v0.2.3)

## 장점

- 밤새 멈춘 세션을 감지하고 복구 시도 또는 알림으로 회수합니다.
- 자율 모드에서 입력대기 질문을 로컬 지침 또는 안전한 기본 응답으로 처리합니다.
- 새 headless 작업을 만들지 않고 현재 대화/구독/컨텍스트를 유지합니다.
- 대상이 불확실하면 키를 보내지 않습니다.
- CLI는 tmux pane 직접 전달을 지원합니다.
- macOS 네이티브 메뉴바 pet/overlay로 상태를 보여줍니다.
- Python 3.9+ stdlib 기반이라 런타임 의존성이 없습니다.
- `NONYA_LANG`와 OS locale로 다국어 UI를 처리합니다.

## 동작 방식 요약

- 앱 실행만으로는 실제 감시 코어가 시작되지 않습니다. 메뉴에서 **감시 시작**을 누르면 `nonya --all` 코어가 실행됩니다.
- **자율모드** 체크는 시작 스위치가 아니라 감시 중 입력대기까지 처리할지 정하는 동작 방식입니다.
- 기본 감시는 화면 캡처가 아니라 Claude/Codex transcript와 로그를 읽는 방식입니다.
- CLI + tmux는 pane id로 직접 `tmux send-keys`를 보내므로 창이 뒤에 있거나 포커스가 없어도 동작합니다.
- tmux가 아닌 일반 터미널 split은 오주입 위험 때문에 기본적으로 알림만 보냅니다.
- GUI 앱은 사용자가 자리를 비운 경우에만 앱을 앞으로 가져오고, ScreenCaptureKit + Vision OCR로 대상 대화를 확인한 뒤 좌표 클릭/붙여넣기/전송 검증을 합니다.
- 대상이 없거나 애매하면 키를 보내지 않습니다.

자세한 설명은 [HOW-IT-WORKS.ko.md](HOW-IT-WORKS.ko.md)를 보세요.

## 빠른 시작

```bash
git clone https://github.com/ezBuilder/nonya.git
cd nonya
./install.sh
nonya --check
nonya --target cli --tmux %3 --engine claude
```

## 안전 원칙

실계정 Claude/Codex GUI 앱은 항상 알림만인 것은 아닙니다. Watch all 스캐너는 사용자가 자리를 비웠고 ScreenCaptureKit + Vision OCR로 대상 대화를 증명할 수 있을 때만 조건부로 개입합니다. 대상이 애매하거나 raw terminal split이면 알림만 보냅니다. 단일 세션 직접 앱 주입과 실계정 스모크 테스트에는 `NONYA_ALLOW_REAL_APP_INJECT=1`이 필요하며, 테스트 스모크에는 `NONYA_REAL_APP_INJECT_CONFIRM=TYPE_INTO_REAL_AGENT_APP`도 필요합니다.

## 다국어

지원 언어: `en`, `ko`, `ja`, `zh-Hans`, `zh-Hant`, `es`, `fr`, `de`, `pt-BR`.

```bash
NONYA_LANG=ko nonya --metrics
```

자세한 지원 표면은 [TARGET-MATRIX.md](TARGET-MATRIX.md)를 보세요.
