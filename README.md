# 競馬予想プログラム（クラウド運用版）

中央競馬の重賞レースを毎週末に自動で予想し、メールで配信する。
GitHub Actions 上で動くので、**PC の電源が入っているかどうかに関係なく動く**。

## 何が動くのか

| ワークフロー | 実行タイミング | 中身 |
|---|---|---|
| `予想` (`predict.yml`) | 土・日 08:00 JST | 今週末の重賞を予想 → メール送信 → `data/predictions/` にコミット |
| `答え合わせ` (`review.yml`) | 月 09:00 JST | 先週の結果を取得 → 的中率・回収率を計算 → `data/accuracy.md` を更新 |
| `テスト` (`test.yml`) | push / PR ごと | ネット接続なしでパーサと評価ロジックを検証 |

いずれも Actions 画面の「Run workflow」から手動実行もできる。

## 初期設定（1回だけ）

### 1. メール送信用の Secrets を登録する

リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で以下を登録する。

| 名前 | 値 | 必須 |
|---|---|---|
| `SENDER_EMAIL` | 送信元の Gmail アドレス | ✅ |
| `SENDER_PASSWORD` | Gmail の**アプリパスワード**（通常のログインパスワードではない） | ✅ |
| `RECEIVER_EMAIL` | 予想を受け取るアドレス | ✅ |
| `SMTP_SERVER` | 既定は `smtp.gmail.com` | － |
| `SMTP_PORT` | 既定は `587`（`465` にすると SSL 接続） | － |

Gmail のアプリパスワードは、Google アカウントで 2 段階認証を有効にしたうえで
[アプリ パスワード](https://myaccount.google.com/apppasswords) から 16 桁を発行する。
発行した文字列をそのまま `SENDER_PASSWORD` に入れる。

### 2. Actions に書き込み権限を与える

**Settings → Actions → General → Workflow permissions** で
**Read and write permissions** を選ぶ。予想と結果をリポジトリにコミットするために必要。

### 3. 動作確認

Actions タブ →「予想」→ Run workflow で手動実行する。
メールが届き、`data/predictions/` に JSON が増えていれば成功。

## 予想メソッドの更新の仕方

高配当を狙うには、当たったか外れたかを記録して調整を繰り返す必要がある。
そのための仕組みが入っている。

1. `data/accuracy.md` で現在の回収率を確認する
2. `analyzer.py` の `WEIGHTS` を調整する（血統・騎手・戦績・調子の配点）
3. **`METHOD_VERSION` を上げる**（`v2` → `v3`）
4. push する

以降の結果はバージョンごとに集計されるので、
`data/accuracy.md` の表を見れば変更が効いたかどうかが分かる。

集計の前提は「◎に単勝100円 ＋ ◎○▲△に複勝100円ずつ ＋ 穴候補に単勝100円」。
**回収率が 100% を超えていれば理論上プラス**で、下回っている間は調整を続けることになる。

### 高配当を狙う仕組み

スコアが高いのに人気が無い馬（`人気順位 − 評価順位 >= 3`）を「妙味あり」として印を付ける。
そのうち単勝 10 倍以上の馬を「穴候補」として本命とは別枠で出す。
このしきい値は `analyzer.py` の `VALUE_GAP_THRESHOLD` と `LONGSHOT_MIN_ODDS` で変えられる。

## 手元で動かす

```bash
pip install -r requirements.txt

python main.py --today 2026-08-08 --no-email   # 予想だけ表示
python review.py --no-email                    # 答え合わせだけ実行
pytest                                         # テスト（ネット接続不要）
```

## ファイル構成

```
main.py        予想の実行（週末）
review.py      答え合わせの実行（週明け）
scraper.py     netkeiba からの取得とパース
analyzer.py    スコアリング ← 予想メソッドを更新するのはここ
mailer.py      整形とメール送信
store.py       予想・結果の保存と成績集計
data/          予想と結果の履歴（Actions が自動でコミットする）
```

## 運用コスト

- **パブリックリポジトリなら GitHub Actions は無料**。プライベートでも無料枠が月 2,000 分あり、
  1 回の実行は数分なので週 3 回動かしても収まる。
- 外部の有料 API は使っていない（netkeiba の公開ページのみ）。
- PC を起動しっぱなしにする必要がないので、電気代と PC の消耗が減る。

## 注意

- netkeiba のページ構造が変わると取得に失敗する。その場合はワークフローが**赤くなって失敗する**
  （黙って架空の予想をメールしないようにしてある）。`tests/test_keiba.py` の
  フィクスチャを実際の HTML に合わせて直すのが復旧の手順になる。
- スクレイピングは 1 秒間隔・リトライ 3 回に制限してある。間隔を詰めないこと。
- 予想は自動算出であり、的中を保証しない。馬券の購入は自己責任で。
