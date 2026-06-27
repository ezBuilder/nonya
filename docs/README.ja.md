# nonya - 日本語概要

[README](../README.md) | [한국어](README.ko.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

**nonya** は Claude、Codex、Antigravity の作業セッションを監視し、停止やエラーを検知したときに、同じウィンドウまたは同じ tmux pane へ安全に再開指示を送るオープンソースのウォッチドッグです。

## 特長

- 夜間の自律作業が止まったままになるリスクを減らします。
- 既存の会話、サブスクリプション面、コンテキストを維持します。
- 対象が曖昧な場合は入力せず、通知だけにします。
- Claude/Codex CLI は tmux pane への直接送信を利用できます。
- macOS ではネイティブのメニューバー pet/overlay が状態を表示します。
- コアは Python 3.9+ 標準ライブラリのみで動作します。
- `NONYA_LANG` と OS locale で多言語 UI を選択します。

## クイックスタート

```bash
git clone https://github.com/ezBuilder/nonya.git
cd nonya
./install.sh
nonya --check
nonya --target cli --tmux %3 --engine claude
```

## 安全性

実アカウントの Claude/Codex/Antigravity GUI アプリは既定で通知のみです。実アプリへ入力するには `NONYA_ALLOW_REAL_APP_INJECT=1` が必要です。明示的な smoke test には `NONYA_REAL_APP_INJECT_CONFIRM=TYPE_INTO_REAL_AGENT_APP` も必要です。

## ローカライズ

対応 locale: `en`, `ko`, `ja`, `zh-Hans`, `zh-Hant`, `es`, `fr`, `de`, `pt-BR`.

```bash
NONYA_LANG=ja nonya --metrics
```

現在の対応範囲は [TARGET-MATRIX.md](TARGET-MATRIX.md) を参照してください。
