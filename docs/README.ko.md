# nonya (노냐?) - 한국어 소개

[README](../README.md) | [English](README.en.md) | [日本語](README.ja.md) | [简体中文](README.zh-Hans.md)

**노냐?**는 Claude, Codex, Antigravity 같은 AI 작업 세션이 멈췄는지 감시하고, 안전한 경우 같은 창이나 같은 tmux pane에서 이어가기 신호를 보내는 오픈소스 세션 자동복구 도구입니다.

다운로드: [v0.2.1 릴리즈](https://github.com/ezBuilder/nonya/releases/tag/v0.2.1)

## 장점

- 밤새 멈춘 세션을 감지하고 복구 시도 또는 알림으로 회수합니다.
- 자율 모드에서 입력대기 질문을 로컬 지침 또는 안전한 기본 응답으로 처리합니다.
- 새 headless 작업을 만들지 않고 현재 대화/구독/컨텍스트를 유지합니다.
- 대상이 불확실하면 키를 보내지 않습니다.
- CLI는 tmux pane 직접 전달을 지원합니다.
- macOS 네이티브 메뉴바 pet/overlay로 상태를 보여줍니다.
- Python 3.9+ stdlib 기반이라 런타임 의존성이 없습니다.
- `NONYA_LANG`와 OS locale로 다국어 UI를 처리합니다.

## 빠른 시작

```bash
git clone https://github.com/ezBuilder/nonya.git
cd nonya
./install.sh
nonya --check
nonya --target cli --tmux %3 --engine claude
```

## 안전 원칙

실계정 Claude/Codex/Antigravity 앱은 기본적으로 알림만 보냅니다. 실제 앱에 키를 입력하려면 `NONYA_ALLOW_REAL_APP_INJECT=1`을 명시해야 하며, 테스트 스모크에는 `NONYA_REAL_APP_INJECT_CONFIRM=TYPE_INTO_REAL_AGENT_APP`도 필요합니다.

## 다국어

지원 언어: `en`, `ko`, `ja`, `zh-Hans`, `zh-Hant`, `es`, `fr`, `de`, `pt-BR`.

```bash
NONYA_LANG=ko nonya --metrics
```

자세한 지원 표면은 [TARGET-MATRIX.md](TARGET-MATRIX.md)를 보세요.
