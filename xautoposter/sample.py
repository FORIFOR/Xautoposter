"""Entirely invented examples. No real X posts, authors, or incident metrics."""
from datetime import datetime, timedelta, timezone

from .models import ImportBundle


def sample_bundle():
    base = datetime(2026, 9, 12, 6, tzinfo=timezone.utc)
    topics = [
        ("セキュリティの優先順位", [40, 52, 48, 90, 185, 360], [
            "深刻度の点数と、対応の順番は同じでいい？\n公開状態・権限・実際の操作をつないで見る。たとえば、同じ警告でも外から入れる機器と内部の検証機では意味が違う。\n合成ログでこの違いを検証してみた。",
            "セキュリティの運用を見直しています。\nたとえば警告を一覧にして、週ごとに確認しています。",
            "警告が増えてきたので分類しました。\nたとえば機器の種類ごとに整理すると探しやすいです。",
            "今週は権限設定の棚卸しをしました。\nたとえば管理用アカウントを一覧にするところから。",
            "ログの保存期間をチームで相談しました。\nたとえば古いファイルの整理も必要ですね。",
            "アラートの件数だけではなく、つながりを見たらどうなる？\nたとえば公開機器→強い権限→大量アクセスの経路を合成データで検証した。\n単独の警告と比較すると、確認すべき点が変わった。",
            "ログの整備を少しずつ進めています。\nたとえば時刻の形式が揃っていると調べやすい。",
            "警告を減らせば解決する？\nたとえば閾値を上げると、必要な検知も失うことがある。\n合成データで失敗例を比較してみた。",
        ]),
        ("個人開発の検証", [28, 32, 42, 46, 55, 72], [
            "作りたい機能より、使う理由を先に確かめる。\nたとえば画面を作る前に、困ったときの作業を見せてもらう。\n小さな実験で分かったことを記録しておきたい。",
            "個人開発の進捗です。画面の余白を直しました。",
            "新しいフォームを追加しました。少し使いやすくなりました。",
            "開発環境を更新しました。週末も少し進めます。",
            "昨日は設定画面を作りました。引き続き改善します。",
            "機能を増やしても使われなかった。\nたとえば入力までの手順を比べると、最初の画面で迷うことが分かった。\n説明を減らす実験をしてみた。",
        ]),
        ("AIの評価と実務", [62, 72, 81, 79, 71, 68], [
            "デモで動くことと、仕事で使えることは違う。\nたとえば失敗した入力を残して、条件を変えて比較する。\n評価の仕組みを先に作ってみた。",
            "AIツールを試しました。作業のメモをまとめています。",
            "今日もプロンプトを調整しました。続けて試します。",
            "文章の要約を試しています。結果を読み返しています。",
            "AIの導入メモを共有しました。来週も検証します。",
            "正解率だけで比較していい？\nたとえば失敗したときの修正時間も測定すると見方が変わった。\n小さな実験を積み重ねたい。",
        ]),
    ]
    posts, counts = [], []
    for topic_index, (topic, volume, texts) in enumerate(topics):
        for i, text in enumerate(texts):
            created = base - timedelta(hours=3 if i == 0 else 5 + i * 2)
            high = i in {0, 5, 7}
            final = (480 + i * 90 + topic_index * 50) if high else 35 + i * 4
            observations = []
            for age, factor in [(15, .015), (60, .09), (120, .35), (180, 1)]:
                likes = int(final * factor)
                observations.append({"observed_at": (created + timedelta(minutes=age)).isoformat(),
                    "like_count": likes, "retweet_count": int(likes * .22), "reply_count": int(likes * .1),
                    "quote_count": int(likes * .03), "bookmark_count": int(likes * .18), "impression_count": likes * 65})
            posts.append({"id": f"demo-{topic_index}-{i}", "author_id": f"demo-researcher-{topic_index}" if i < 5 else f"demo-peer-{topic_index}-{i}",
                          "author_name": f"架空の観測者 {topic_index + 1}" if i < 5 else f"架空の開発者 {i}", "text": text,
                          "created_at": created.isoformat(), "lang": "ja", "media_type": "text", "topic": topic,
                          "source_query": f"{topic} lang:ja -is:retweet -is:reply", "snapshots": observations})
        for j, count in enumerate(volume):
            end = base - timedelta(hours=5-j)
            counts.append({"topic": topic, "query": f"{topic} lang:ja -is:retweet -is:reply", "start": (end - timedelta(hours=1)).isoformat(),
                           "end": end.isoformat(), "count": count, "observed_at": base.isoformat(), "complete": True})
    replies = ["確かに、点数だけでは判断しにくいですね。", "私も現場で似た警告を調べた経験があります。", "優先順位は具体的にどう決めていますか？", "訂正です。合成データの結果を実運用の精度とは呼べません。", "その条件だけで決まるとは限らないと思います。", "複数の機器をまたぐ場合はどうするのでしょうか？", "資料を読みました。", "私もログをつなぐところで苦労しました。"]
    for i, text in enumerate(replies):
        posts.append({"id": f"demo-response-{i}", "author_id": f"demo-reader-{i}", "author_name": f"架空の読者 {i+1}", "text": text,
                      "created_at": (base - timedelta(minutes=45-i)).isoformat(), "lang": "ja", "media_type": "text",
                      "topic": topics[0][0], "kind": "quote" if i > 5 else "reply", "parent_id": "demo-0-0",
                      "snapshots": [{"observed_at": base.isoformat(), "like_count": i, "retweet_count": 0, "reply_count": 0, "quote_count": 0}]})
    posts.append({"id": "demo-insufficient", "author_id": "demo-unknown", "author_name": "観測不足のサンプル",
                  "text": "一度だけ取得した投稿です。現在の総数から初速を逆算してはいけません。", "created_at": (base-timedelta(days=2)).isoformat(),
                  "lang": "ja", "media_type": "unknown", "topic": topics[0][0],
                  "snapshots": [{"observed_at": base.isoformat(), "like_count": 2000}]})
    return ImportBundle.model_validate({"name": "探索デモ · 合成データ", "provenance": "synthetic", "posts": posts, "counts": counts,
                                      "complete": False, "scope": "2026-09-12の架空データ。人物・投稿・反応・件数はすべて合成。最新のXを観測した結果ではありません。"})
