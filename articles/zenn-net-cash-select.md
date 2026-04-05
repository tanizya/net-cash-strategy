---
title: "Claude Code + TradingView MCP で日本株のバリュー戦略ダッシュボードを自動化した"
emoji: "📊"
type: "tech"
topics: ["claudecode", "tradingview", "python", "github"]
published: false
---

## なぜAIにやらせるのか

トレード戦略で一番難しいのは、ルールを守ること。

「RSIが35を下回るまで待つ」——言うのは簡単だが、含み損を見ながら何週間も待つのは人間には辛い。逆に、シグナルが出ていないのに「なんとなく安そう」で買ってしまう。損切りラインを「もう少し待てば戻るかも」と動かしてしまう。

少ないトレード、厳しいフィルター、機械的な損切り。これらの規律を守るのが人間は苦手だ。

考えてみれば当然で、人間は「感情を持つ」という致命的なバグを抱えている。含み損で不安になり、含み益で欲が出る。チャートを見すぎて寝不足になる。決算前にソワソワしてポジションを閉じる。そしてその銘柄が翌日ストップ高になる。

アルゴリズムにはこれらの問題が一切ない。眠くならない。焦らない。「今回だけは特別」と言わない。人間よりトレードが上手いに決まっている——少なくとも、ルールを守るという一点においては。

だからAIに戦略の開発から運用まで全部やらせてみた。

## 作ったもの

日本株のディープバリュー戦略ダッシュボード。毎日自動更新。

https://tanizya.github.io/net-cash-strategy/

**やっていること:**
- 東証3,000銘柄からネットキャッシュ（現金 > 借金）の株を自動抽出
- スコアリングで上位8銘柄を選出
- RSIで「売られすぎ」のタイミングで買い、戻ったら売り
- シグナル・ポートフォリオ・チャートをダッシュボードで表示

## 結果（2025年バックテスト）

200万円スタート、税引後（20.315%）:

| | |
|---|---|
| **税引後リターン** | **+50.7%** |
| **税引後利益** | **+101万円** |
| **勝率** | 89.7%（26勝3敗） |
| **最大DD** | 6.7% |
| **プラス銘柄** | 8/8 |

## 戦略

```
銘柄選定: JPX全銘柄 → ネットキャッシュ正 → 赤字除外 → スコア順
スコア:   利益率25% + モメンタム25% + キャッシュ15% + PBR15% + Beta10%
Entry:    RSI(14) が 35 を上抜け → 翌寄りで買い
Exit:     RSI >= 65 / SMA(20)割れ / ストップロス
Stop:     NI225 > SMA50 → 10%、NI225 < SMA50 → 7%
```

NI225はエントリーフィルターではなく、ストップロスの動的調整にだけ使う。エントリーでフィルタリングすると暴落後の反発を逃して逆効果になる。これが一番の学び。

## 技術スタック

```
Dashboard:   HTML + Lightweight Charts + JetBrains Mono
Data:        yfinance + JPX上場銘柄リスト(xls)
Tags:        Claude API (Haiku) で定性タグ自動生成
DB:          Supabase (jpx_stocks テーブル、約600銘柄)
Automation:  GitHub Actions (毎日17時 + 毎月28日)
Strategy:    TradingView Pine Script v6
Dev:         Claude Code + TradingView MCP
```

## 自動化フロー

**毎日（GitHub Actions）:**
yfinance → RSI・シグナル計算 → data.json更新 → GitHub Pages再デプロイ

**毎月28日（GitHub Actions）:**
JPXリスト → 3,000銘柄スキャン → ネットキャッシュ約600銘柄をSupabase登録 → 上位8銘柄選出 → Claude APIでタグ生成

## Claude Code + TradingView MCP

今回の開発はほぼ全てClaude Codeで行った。TradingView MCPでチャートを直接操作できるので、Pine Script開発→コンパイル→バックテスト→結果分析が会話の中で完結する。

NI225フィルターの試行錯誤（4回失敗→5回目で正解）も、Claude Codeがバックテスト結果を見ながら「この戦略では暴落フィルターは逆効果」と分析してくれたのが大きかった。

---

- **Dashboard**: https://tanizya.github.io/net-cash-strategy/
- **GitHub**: https://github.com/tanizya/net-cash-strategy

*免責: 投資助言ではありません。バックテスト結果は将来の成績を保証しません。*
