# nonya - 简体中文概览

[README](../README.md) | [한국어](README.ko.md) | [English](README.en.md) | [日本語](README.ja.md)

**nonya** 会监控 Claude、Codex、Antigravity 等 AI 工作会话。当会话卡住、报错或停止推进时，它会在安全确认后向同一个窗口或同一个 tmux pane 发送继续指令，让原来的对话继续运行。

下载: [v0.2.4 release](https://github.com/ezBuilder/nonya/releases/tag/v0.2.4)

## 优点

- 减少夜间自动工作停在错误页面的情况。
- auto mode 会用本地规则或保守安全默认值处理输入等待。
- 保留当前对话、订阅入口和上下文。
- 目标不明确时不会输入，只发送通知。
- Claude/Codex CLI 可通过 tmux pane 直接投递。
- macOS 原生菜单栏 pet/overlay 可显示运行状态。
- 核心运行时只依赖 Python 3.9+ 标准库。
- 通过 `NONYA_LANG` 和 OS locale 选择多语言 UI。

## 快速开始

```bash
git clone https://github.com/ezBuilder/nonya.git
cd nonya
./install.sh
nonya --check
nonya --target cli --tmux %3 --engine claude
```

## 安全原则

真实账号的 Claude/Codex GUI 应用并不是简单的“只通知”。Watch all scanner 只会在用户离开、且 ScreenCaptureKit + Vision OCR 能证明目标会话时有条件介入。目标不明确、raw terminal split、Antigravity GUI 仍然只通知。单会话直接 GUI 输入和显式 smoke test 需要 `NONYA_ALLOW_REAL_APP_INJECT=1`；smoke test 还需要 `NONYA_REAL_APP_INJECT_CONFIRM=TYPE_INTO_REAL_AGENT_APP`。

## 本地化

支持的运行时 locale: `en`, `ko`, `ja`, `zh-Hans`, `zh-Hant`, `es`, `fr`, `de`, `pt-BR`.

```bash
NONYA_LANG=zh-Hans nonya --metrics
```

当前支持范围见 [TARGET-MATRIX.md](TARGET-MATRIX.md)。
