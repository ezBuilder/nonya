# nonya - 日本語概要

[README](../README.md) | [한국어](README.ko.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

**nonya** は Claude、Codex、Antigravity の作業セッションを監視し、停止やエラーを検知したときに、同じウィンドウまたは同じ tmux pane へ安全に再開指示を送るオープンソースのウォッチドッグです。

ダウンロード: [v0.2.5 release](https://github.com/ezBuilder/nonya/releases/tag/v0.2.5)

## 特長

- 夜間の自律作業が止まったままになるリスクを減らします。
- auto mode では入力待ちの質問をローカル指針または安全な既定回答で処理します。
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

Claude/Codex の実アカウント GUI アプリは、単純な「通知のみ」ではありません。Watch all scanner は、ユーザーが離席中で ScreenCaptureKit + Vision OCR により対象会話を証明できる場合だけ条件付きで介入します。曖昧な対象、raw terminal split、Antigravity GUI は通知のみです。単一セッションの直接 GUI 入力と明示的な smoke test には `NONYA_ALLOW_REAL_APP_INJECT=1` が必要で、smoke test には `NONYA_REAL_APP_INJECT_CONFIRM=TYPE_INTO_REAL_AGENT_APP` も必要です。

## ローカライズ

対応 locale: `en`, `ko`, `ja`, `zh-Hans`, `zh-Hant`, `es`, `fr`, `de`, `pt-BR`.

```bash
NONYA_LANG=ja nonya --metrics
```

現在の対応範囲は [TARGET-MATRIX.md](TARGET-MATRIX.md) を参照してください。
