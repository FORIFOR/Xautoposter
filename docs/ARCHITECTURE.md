# Xautoposter P0 — 独立したX分析アプリ

## 実装範囲

2026-09-13の最終指示に従い、Launchloom拡張・主張台帳・3投稿生成を撤回し、Xautoposter内に独立実装した。Launchloomのコード・DB・設定は読み書きしない。

```
公式X API GET ─ collectors.py ─┐
CSV / JSON ── importing.py ───┼─ models.py ─ store.py (SQLite)
明示的な合成データ ─ sample.py ┘                  │
                                        analysis.py（数値）
                                        text_analysis.py（文章・仮説）
                                                │
                                        server.py → web/
                                                │
                                         分析スナップショット保存 / JSON
```

収集・数値分析・文章解釈・保存／出力が分かれており、生成・公開・計測のダミー画面はない。文章解析にはネットワーク・秘密・コード実行・X操作の権限がない。将来LLMを接続する場合も数値・比較選択・公開権限をこのモジュールに持たせない。

## 主なファイル

| ファイル | 責務 |
| --- | --- |
| `models.py` | 入力、指標のnull、タイムゾーン、投稿／観測の前後関係、設定上限 |
| `store.py` | SQLiteトランザクション、データ種別、重複制約、本文履歴、保存した分析、カーソル、費用台帳 |
| `collectors.py` | api.x.comのGETのみ、検索・ID再観測・件数・返信／引用、間隔・429・定期処理 |
| `importing.py` | CSV／JSONの解析、バッチ検証。外部URLは取得しない |
| `analysis.py` | 実測速度、登録テーマの件数窓、経過時間をそろえた比較、構成の群比較 |
| `text_analysis.py` | 語句による構成・反応分類、比較根拠に結び付いた仮説、次の検証論点 |
| `sample.py` | 実在投稿を含まない固定の合成データ |
| `server.py` | localhost・オリジン・起動ごとのトークン境界、ローカルAPI、静的画面 |
| `web/` | レーダー・投稿詳細・ライブラリ・収集設定・取り込み。ビルド不要、外部CDNなし |
| `scripts/browser_smoke.py` | 独立プロセス・一時DBでのChromium受入検証 |

## 保存モデル

SQLiteの `user_version=1`。このP0は新規アプリで既存DBの移行元はない。

- `datasets`：`synthetic` / `imported` / `x_api`。比較は常に単一データセット内。
- `posts`：データセット＋投稿IDが主キー。本文・作成日時・作者・言語・形式・返信関係。
- `snapshots`：データセット＋投稿ID＋UTC観測時刻が主キー。欠損はJSON `null`。
- `memberships`：投稿と登録テーマ・収集条件の多対多対応。複数検索で同一投稿を得ても観測を複製しない。
- `counts`：登録テーマ＋検索式＋時間窓＋観測時刻。過去の取得記録を残し、分析では各窓の最終記録を使用。異なる検索式は合算しない。
- `post_revisions`：本文・属性が更新された場合の旧値。
- `saved`：保存時点の分析JSON、比較対象ID／時刻、メモ。更新操作時のみ置き換え。
- `imports` / `runs`：取得範囲、完了性、取得件数、エラー。
- `sources`：条件と継続カーソル。条件の意味を変えた編集ではカーソルを破棄。
- `ledger`：要求前に予算を予約。返却件数確認後に精算。不確実な要求の予約は保持。
- `config`：停止・上限・レート制限時刻。Bearer Tokenは保存しない。

数値の矛盾はバッチ全体をロールバックする。費用予約は `BEGIN IMMEDIATE` 内で日次・月次・資源数を再確認し、同時要求でも上限を共有する。単一プロセス内では収集・再観測を同じロックで直列化する。

## 公式読み取り経路

| 用途 | エンドポイント |
| --- | --- |
| キーワード／アカウントの新着 | `GET /2/tweets/search/recent` |
| 時間別の話題件数 | `GET /2/tweets/counts/recent` |
| 投稿URL／ID・保存投稿の再観測 | `GET /2/tweets?ids=...` |
| 返信標本 | recent searchの `conversation_id:... is:reply` |
| 引用標本 | `GET /2/tweets/{id}/quote_tweets` |

X公式Quickstartの `tweet.fields` 形式を使用し、新旧の `tweet_count` / `post_count`、`referenced_tweets` / `referenced_posts` の応答名を正規化する。作者IDは本文フィールドから取得し、ユーザー展開を要求して追加のUser Read費用を発生させない。実キー・契約に対するフィールド／エンドポイントの受理確認は未実施。

接続先は固定。リダイレクト禁止、HTTP環境プロキシ不使用、25秒タイムアウト、5MB応答上限。エラー本文やTokenを画面・DBに記録しない。書き込みメソッドはない。

## ローカルAPI

全APIは `/api` 配下。起動ごとの `X-Local-Token` を要求（同一オリジンの `/api/session` で取得）。CORSは有効化しない。Hostはlocalhost系のみ許可し、異なるOrigin・cross-site Fetch Metadataを拒否。CSPでスクリプト・通信先をselfに限定する。

- `GET /datasets`、`POST /demo`、`POST /import`
- `GET /import-template/{csv|json}`
- `GET /datasets/{dataset}/overview`
- `GET /datasets/{dataset}/posts/{id}`
- `GET|PUT /datasets/{dataset}/posts/{id}/saved`
- `GET|PUT /settings`、`GET|POST /sources`、`PUT|DELETE /sources/{id}`
- `POST /sources/{id}/collect`
- `POST /observe/{id}`、`POST /observe-saved`、`POST /responses/{id}`

ローカルPOSTは取り込み・読み取り取得の開始操作であり、XへのPOSTではない。分析画面・JSON出力からX API取得は起動されない。追加した自分の発信画面には、利用者が申告した公開URLを記録するモデルがある。公開実行モデル・Xへの送信経路はない。

## 比較の意味

比較は登録テーマ単位であり、意味的な自動クラスタリングではない。アカウント全体を一つの条件にした場合は主題のばらつきが残るため、画面にも限界を示す。元投稿だけを通常比の対象とし、返信・引用は混ぜない。本文更新の検出された投稿も構成比較から除く。

観測→分類→仮説の根拠連鎖は保存されるが、無作為標本・因果推論・実際の支持率を提供するものではない。LLM接続・機械学習・ファインチューニングは実装していない。

## 追加：自分の発信・改善

`experiments.py` はローカルの下書き・公開URL申告・手動観測・振り返り・改善元との親子関係を扱う。ネットワークや配信の権限を持たない。`web/experiments.js` / `experiments.css` が画面を担当。APIは `/api/experiments` 以下で同じオリジン・セッショントークン境界に従う。

- 既存DBに `experiments` と `experiment_history` を `CREATE TABLE IF NOT EXISTS` で追加。既存のレーダー・観測データは変更しない。
- バージョン番号と `BEGIN IMMEDIATE` で古い画面からの上書きを拒否。変更履歴には各時点の全文を保存。
- 公開URL登録後は本文・仮説・評価指標・経過時間を固定。URLは投稿IDに正規化して重複を拒否。登録はユーザー申告で、X上での成功確認ではない。
- 観測は日時とnullable指標、返信・引用の自由記述を保存。同時刻の矛盾を上書きせず、完全一致は重複追加しない。
- 数値は指定した投稿経過時間付近の実測のみ。比較の標本・時刻・条件・改善元との差を返す。X API由来のデータとは自動同期も混合比較もしない。
- 振り返りの保存時に観測・比較レポートを固定し、後の観測追加で要再確認。改善版は保存済みの振り返りを複製して紐づける。
- JSON出力・XへのWeb Intentリンクは公開状態を変えない。LLM生成・自動公開・自動フォローアップはこの追加では実装していない。
