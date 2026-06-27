# nonya (노냐?) - AI session watchdog

<p align="center">
  <img src="assets/marketing/nonya-social-card.png" alt="nonya promotional card" width="920">
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-0f766e.svg"></a>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-2563eb.svg">
  <img alt="stdlib only" src="https://img.shields.io/badge/runtime-stdlib%20only-111827.svg">
  <img alt="macOS + Windows" src="https://img.shields.io/badge/platform-macOS%20%2B%20Windows-475569.svg">
</p>

**Languages:** [한국어](docs/README.ko.md) | [English](docs/README.en.md) | [日本語](docs/README.ja.md) | [简体中文](docs/README.zh-Hans.md)

[Download v0.2.5](https://github.com/ezBuilder/nonya/releases/tag/v0.2.5) | [How it works](docs/HOW-IT-WORKS.ko.md) | [Card news](assets/marketing/cardnews/)

**노냐?**는 Claude, Codex, Antigravity 같은 AI 작업 세션이 밤새 멈췄는지 감시하고, 안전하다고 확인된 경우 같은 창 또는 같은 tmux pane에서 작업을 다시 이어가게 하는 오픈소스 세션 자동복구 도구다.

Headless 재시작이 아니라 사용 중인 대화 표면을 그대로 살린다. 구독, 컨텍스트, 진행 중인 작업을 최대한 유지하면서 "멈췄으면 깨우고, 애매하면 위험하게 승인하지 않는다"는 보수적 원칙을 지킨다. `--mode auto`에서는 입력대기도 로컬 지침 또는 안전한 기본 응답으로 처리해 밤샘 작업이 질문 하나에 멈춰 서지 않게 한다.

## Why nonya

- **야간 자율 작업 회수율**: 에러, rate limit, crash, 계약된 `<<DONE>>` 미완료를 감지해 재시도하거나 사람에게 알린다.
- **입력대기 자동 처리**: 자율 모드에서 루틴 질문은 안전한 로컬 기본값으로 답해 세션이 놀지 않게 한다.
- **현재 세션 보존**: 새 headless job을 만들지 않고 떠 있는 GUI/CLI 세션을 기준으로 복구한다.
- **안전 게이트 우선**: 다중창, 타겟 불확실, 권한 부족, 질문/승인 대기 상태에서는 키를 보내지 않고 알림만 보낸다.
- **tmux 직접 전달**: Claude CLI / Codex CLI는 foreground focus 없이 정확한 pane으로 `send-keys` 전달을 검증했다.
- **네이티브 macOS 펫**: 상태를 보여주는 메뉴바/투명 overlay pet이 코어와 JSON 상태 파일로 느슨하게 연결된다.
- **의존성 0 코어**: Python 3.9+ stdlib만 사용한다. 패키징은 별도 빌드 단계에서만 필요하다.
- **다국어 UI**: `NONYA_LANG`와 OS locale을 기반으로 `en`, `ko`, `ja`, `zh-Hans`, `zh-Hant`, `es`, `fr`, `de`, `pt-BR`을 처리한다.

## How it works

앱 실행과 감시 시작은 다르다. macOS 메뉴바 앱은 먼저 UI shell로 뜨고, 메뉴에서 **감시 시작**을 눌러야 번들된 `nonya --all` 코어가 실제 감시를 시작한다. **자율모드** 체크는 시작 스위치가 아니라 감시가 켜졌을 때 `--mode auto`로 입력대기까지 처리할지 정하는 동작 방식 설정이다. Claude hook의 `NONYA_AUTOSTART=1` 또는 launchd 에이전트를 따로 켠 경우만 버튼 없이 시작될 수 있다.

기본 감시는 화면 캡처가 아니라 transcript 기반이다.

1. Claude/Codex JSONL, Antigravity SQLite/log, 또는 지정 transcript의 최신 기록과 idle 상태를 본다.
2. 내용을 `ERROR`, `RATE_LIMIT`, `TOOL_PENDING`, `COMPLETED`, `IDLE_WAIT`, `STALLED`로 분류한다.
3. 세션별로 "같은 대상에 안전하게 보낼 수 있는가"를 먼저 판단한다.
4. CLI/tmux는 pane id로 직접 `tmux send-keys`를 보내므로 창이 뒤에 있거나 포커스가 없어도 동작한다.
5. raw terminal split은 오주입 위험 때문에 기본적으로 알림-only다.
6. GUI 앱은 사용자가 자리를 비운 경우에만 앱을 앞으로 가져오고 ScreenCaptureKit + Vision OCR로 사이드바/헤더를 읽어 대상 대화를 증명한 뒤 좌표 클릭, 붙여넣기, 전송 검증을 수행한다. 애매하면 키를 보내지 않는다.
7. 정상 종료된 세션은 건드리지 않는다. `<<DONE>>`을 요구한 최근 사용자 지시가 있을 때만 미완료 keep-going으로 본다.
8. 진행 재개, 검증 통과, 반복 실패, 사람 승인 필요를 ledger와 알림으로 남긴다.

자세한 동작 방식은 [docs/HOW-IT-WORKS.ko.md](docs/HOW-IT-WORKS.ko.md)에 정리되어 있다.

## Safety model

핵심 불변식: **타겟을 확신하지 못하면 키를 보내지 않는다.**

| Target | macOS behavior | Windows behavior |
|---|---|---|
| Claude CLI / Codex CLI in tmux | automatic pane delivery, verified | tmux/WSL path planned and guarded |
| Claude App | conditional GUI recovery: unattended + OCR target proof + verification; direct single-session real-app typing tests require `NONYA_ALLOW_REAL_APP_INJECT=1` | same-integrity Win32 path, guarded |
| Codex App | conditional GUI recovery like Claude; Codex thread deep-link focus is separate; direct real-app typing tests require `NONYA_ALLOW_REAL_APP_INJECT=1` | visibility still needs live proof |
| Antigravity App | alert-only by default; opt-in real-app typing only | same-integrity Win32 path, guarded |
| multiple windows / unclear target | alert only | alert only |

macOS GUI injection tests use the disposable `NonyaProbe` app, not a real account app. Real Claude/Codex app typing smoke tests require both:

```bash
NONYA_ALLOW_REAL_APP_INJECT=1 \
NONYA_REAL_APP_INJECT_CONFIRM=TYPE_INTO_REAL_AGENT_APP \
tests/live_real_app_optin.sh Claude
```

## Install

Developer install requires Python 3.9+ and no extra runtime packages.

End-user macOS build:

```bash
open https://github.com/ezBuilder/nonya/releases/tag/v0.2.5
```

```bash
git clone https://github.com/ezBuilder/nonya.git
cd nonya
./install.sh
nonya --check
```

Optional integrations:

```bash
./install.sh --hooks    # Claude SessionStart/End hook wiring; autostart is opt-in
./install.sh --launchd  # macOS restart/crash persistence
```

Native Windows:

```bat
python -m nonya --check
bin\nonya.cmd --target claude
```

Uninstall:

```bash
./uninstall.sh
```

## Usage

```bash
nonya --target claude
nonya --target claude --mode auto
nonya --target codex
nonya --target antigravity
nonya --target cli --tmux %3 --engine claude
nonya --target cli --app Ghostty --engine codex --file <transcript>
nonya --target claude --dry-run
NONYA_NTFY_TOPIC=<topic> nonya --target cli --tmux %3 --engine claude
```

`--mode auto` also handles input-waiting: low-risk Claude Code tool permission
prompts can be auto-approved by the bundled PreToolUse hook, plain questions use
local guidance such as `AGENTS.md`, `CLAUDE.md`, or `README.md` when available,
and otherwise nonya sends a conservative "continue with the safest reversible
local default" answer. It never grants unattended approval for secrets, billing,
destructive actions, package installs, external network, privilege escalation,
production, deploy, publish, or release actions.

Useful options:

```text
--mode on-error|auto
--idle <seconds>
--grace <seconds>
--poll <seconds>
--hang-cap <seconds>
--tmux <pane>
--max-nudges <n>
--max-hours <hours>
--nudge "custom text"
```

## Localization

Runtime language resolution:

1. `NONYA_LANG`
2. `LC_ALL`, `LC_MESSAGES`, `LANG`
3. macOS preferred UI language
4. English fallback

Examples:

```bash
NONYA_LANG=ko nonya --metrics
NONYA_LANG=ja nonya --target claude --dry-run
NONYA_LANG=zh-Hans nonya --check
```

The catalog lives in [nonya/i18n.py](nonya/i18n.py). Integrity is checked by [tests/test_i18n.py](tests/test_i18n.py).

## Native pet and character assets

The macOS shell in [macos/](macos/) builds a transparent menu-bar pet/overlay that mirrors core status from `~/.local/state/nonya/state.json`. User-provided AI character models are intentionally excluded from git:

- Drop generated `.glb` files into `models/incoming/`.
- Convert/promote into `models/active/` for the local app.
- Keep third-party model licenses documented in [docs/CREDITS.md](docs/CREDITS.md).

See [models/incoming/README.md](models/incoming/README.md) and [docs/RELEASE.md](docs/RELEASE.md).

## Verification

Closest checks for this repo:

```bash
python3 tests/test_i18n.py
python3 tests/test_support_matrix.py
bash tests/e2e.sh
```

Release/package paths:

```bash
bash packaging/build-core.sh
bash packaging/build-app.sh
```

Windows packages are built on Windows, typically through GitHub Actions:

```powershell
.\packaging\build-windows.ps1
```

The workflow at [.github/workflows/windows-package.yml](.github/workflows/windows-package.yml)
produces `nonya-<version>-windows-x64.zip` as an artifact.

The current support contract is tracked in [docs/TARGET-MATRIX.md](docs/TARGET-MATRIX.md).

## Marketing assets

- Social card: [assets/marketing/nonya-social-card.png](assets/marketing/nonya-social-card.png)
- Generated base artwork: [assets/marketing/nonya-hero-base.png](assets/marketing/nonya-hero-base.png)
- Card news deck: [assets/marketing/cardnews/](assets/marketing/cardnews/)

The generated base artwork contains no brand logos or readable UI text. The social card text is composited locally for exact copy.

## Limits

- Authentication, billing exhaustion, and hard provider rate limits cannot be solved by retrying.
- GUI app injection is deliberately conservative. tmux CLI delivery is the reliable automation path today.
- Windows support follows the shared core and Win32 backend design. The package
  workflow builds a Windows x64 CLI zip, while GUI/app injection surfaces still
  need live Windows hardware proof before being advertised as fully verified.
- Code signing, notarization, Accessibility onboarding, and release DMG polish are maintainer release tasks.

## License

MIT. See [LICENSE](LICENSE).

Architecture and research: [docs/ARCH-cross-platform.md](docs/ARCH-cross-platform.md), [docs/PLAN.md](docs/PLAN.md), [docs/RESEARCH-auto-inject-2026-06-19.md](docs/RESEARCH-auto-inject-2026-06-19.md), [docs/RESEARCH-windows-auto-inject-2026-06-19.md](docs/RESEARCH-windows-auto-inject-2026-06-19.md).
