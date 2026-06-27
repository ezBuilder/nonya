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

**노냐?**는 Claude, Codex, Antigravity 같은 AI 작업 세션이 밤새 멈췄는지 감시하고, 안전하다고 확인된 경우 같은 창 또는 같은 tmux pane에서 작업을 다시 이어가게 하는 오픈소스 세션 자동복구 도구다.

Headless 재시작이 아니라 사용 중인 대화 표면을 그대로 살린다. 구독, 컨텍스트, 진행 중인 작업을 최대한 유지하면서 "멈췄으면 깨우고, 애매하면 절대 입력하지 않는다"는 보수적 원칙을 지킨다.

## Why nonya

- **야간 자율 작업 회수율**: 에러, rate limit, idle, crash, unverified completion을 감지해 재시도하거나 사람에게 알린다.
- **현재 세션 보존**: 새 headless job을 만들지 않고 떠 있는 GUI/CLI 세션을 기준으로 복구한다.
- **안전 게이트 우선**: 다중창, 타겟 불확실, 권한 부족, 질문/승인 대기 상태에서는 키를 보내지 않고 알림만 보낸다.
- **tmux 직접 전달**: Claude CLI / Codex CLI는 foreground focus 없이 정확한 pane으로 `send-keys` 전달을 검증했다.
- **네이티브 macOS 펫**: 상태를 보여주는 메뉴바/투명 overlay pet이 코어와 JSON 상태 파일로 느슨하게 연결된다.
- **의존성 0 코어**: Python 3.9+ stdlib만 사용한다. 패키징은 별도 빌드 단계에서만 필요하다.
- **다국어 UI**: `NONYA_LANG`와 OS locale을 기반으로 `en`, `ko`, `ja`, `zh-Hans`, `zh-Hant`, `es`, `fr`, `de`, `pt-BR`을 처리한다.

## How it works

1. Claude/Codex JSONL, Antigravity SQLite/log, 또는 지정 transcript의 idle 상태를 본다.
2. 내용을 `ERROR`, `RATE_LIMIT`, `TOOL_PENDING`, `COMPLETED`, `IDLE_WAIT`, `STALLED`로 분류한다.
3. 가능한 경우 화면/OCR로 "생성 중" 같은 바쁜 상태를 보조 확인한다.
4. 단일창/단일 pane/정확한 대상이 확인된 경우에만 nudge를 보낸다.
5. 진행 재개, 검증 통과, 반복 실패, 사람 승인 필요를 ledger와 알림으로 남긴다.

## Safety model

핵심 불변식: **타겟을 확신하지 못하면 키를 보내지 않는다.**

| Target | macOS behavior | Windows behavior |
|---|---|---|
| Claude CLI / Codex CLI in tmux | automatic pane delivery, verified | tmux/WSL path planned and guarded |
| Claude App | alert-only by default; real-app typing requires `NONYA_ALLOW_REAL_APP_INJECT=1` | same-integrity Win32 path, guarded |
| Codex App | read-only window gate verified; real-app typing requires `NONYA_ALLOW_REAL_APP_INJECT=1` | visibility still needs live proof |
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

The current support contract is tracked in [docs/TARGET-MATRIX.md](docs/TARGET-MATRIX.md).

## Marketing assets

- Social card: [assets/marketing/nonya-social-card.png](assets/marketing/nonya-social-card.png)
- Generated base artwork: [assets/marketing/nonya-hero-base.png](assets/marketing/nonya-hero-base.png)

The generated base artwork contains no brand logos or readable UI text. The social card text is composited locally for exact copy.

## Limits

- Authentication, billing exhaustion, and hard provider rate limits cannot be solved by retrying.
- GUI app injection is deliberately conservative. tmux CLI delivery is the reliable automation path today.
- Windows support follows the shared core and Win32 backend design, but some surfaces still need live hardware proof.
- Code signing, notarization, Accessibility onboarding, and release DMG polish are maintainer release tasks.

## License

MIT. See [LICENSE](LICENSE).

Architecture and research: [docs/ARCH-cross-platform.md](docs/ARCH-cross-platform.md), [docs/PLAN.md](docs/PLAN.md), [docs/RESEARCH-auto-inject-2026-06-19.md](docs/RESEARCH-auto-inject-2026-06-19.md), [docs/RESEARCH-windows-auto-inject-2026-06-19.md](docs/RESEARCH-windows-auto-inject-2026-06-19.md).
