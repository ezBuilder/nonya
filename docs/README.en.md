# nonya - English overview

[README](../README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-Hans.md)

**nonya** watches live Claude, Codex, and Antigravity work sessions. When a session stalls, errors, or stops progressing, it can safely nudge the same window or tmux pane so the original conversation keeps moving.

Download: [v0.2.4 release](https://github.com/ezBuilder/nonya/releases/tag/v0.2.4)

## Why it is useful

- Recovers overnight autonomous work instead of discovering a dead session in the morning.
- Handles input-waiting in auto mode with local guidance or a conservative safe default.
- Leaves normally completed/idle sessions alone unless a recent user prompt explicitly requested `<<DONE>>`.
- Preserves the current conversation, subscription surface, and context.
- Refuses to type when the target is ambiguous.
- Delivers directly to Claude/Codex CLI sessions in tmux.
- Ships a native macOS menu-bar pet/overlay for live status.
- Keeps the core runtime dependency-free: Python 3.9+ stdlib only.
- Localizes UI strings through `NONYA_LANG` and OS locale detection.

## Quick start

```bash
git clone https://github.com/ezBuilder/nonya.git
cd nonya
./install.sh
nonya --check
nonya --target cli --tmux %3 --engine claude
```

## Safety first

Real Claude/Codex GUI apps are not simply "alert-only." The Watch all scanner can intervene conditionally when the user is away and ScreenCaptureKit + Vision OCR prove the exact target conversation. Ambiguous targets and raw terminal splits stay alert-only. Direct single-session real-app injection and explicit smoke tests require `NONYA_ALLOW_REAL_APP_INJECT=1`; the smoke test also requires `NONYA_REAL_APP_INJECT_CONFIRM=TYPE_INTO_REAL_AGENT_APP`.

## Localization

Supported runtime locales: `en`, `ko`, `ja`, `zh-Hans`, `zh-Hant`, `es`, `fr`, `de`, `pt-BR`.

```bash
NONYA_LANG=en nonya --metrics
```

See [TARGET-MATRIX.md](TARGET-MATRIX.md) for the current support contract.
