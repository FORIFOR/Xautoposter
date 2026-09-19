"""Conservative local text analysis. No LLM, tools, secrets, or code execution.

Numbers and cohort selection live in analysis.py. A future text model can replace
this module's annotations, never the numeric evidence or permission boundary.
"""
import re

FEATURE_RULES = {
    "問いかけ": r"[?？]|どうすれば|なぜ|why\b|how\b",
    "数値への言及": r"\d+(?:[.,]\d+)?(?:%|％|件|分|時間|倍|社|人|\b)",
    "対比": r"ではなく|一方|しかし|でも|よりも|instead|\bbut\b|versus",
    "経験・実験への言及": r"試し|試す|検証|実験|測定|作った|作って|失敗|tested|experiment|built",
    "出典リンク": r"https?://[^\s]+",
    "具体例": r"たとえば|例えば|具体的|for example|such as",
}


def structure(text, media_type):
    sentences = [s.strip() for s in re.split(r"(?<=[。!?！？])|\n+", text) if s.strip()]
    features = []
    for name, regex in FEATURE_RULES.items():
        evidence = next((s for s in sentences if re.search(regex, s, re.I)), None)
        if evidence:
            features.append({"name": name, "evidence": evidence[:500]})
    if media_type in {"image", "video", "mixed"}:
        features.append({"name": "画像・動画", "evidence": f"取得した投稿属性: {media_type}。内容自体は未解析。"})
    return {"method": "ルール解析（LLM未使用・要確認）", "opening": sentences[0] if sentences else text,
            "body": sentences[1:-1], "closing": sentences[-1] if len(sentences) > 1 else None,
            "features": features, "note": "文章に何が書かれているかを分類しています。記述の真偽は保証しません。"}


REACTION_RULES = [
    ("訂正", r"訂正|誤情報|事実と違|正確には|誤り|デマ|correction|incorrect|misleading"),
    ("反論", r"反対(?:です|します|だ|する)|同意でき(?:ない|ません)|違うと思|とは限ら|疑問が|disagree|not convinced"),
    ("質問", r"[?？]|教えて|どうすれば|でしょうか|why\b|how\b"),
    ("経験談", r"私も|自分も|うちも|弊社|現場で|経験|we tried|my experience"),
    ("同意", r"同意でき(?:ます|る)|同感|たしかに|確かに|その通り|賛成|わかる|agree|exactly"),
]

NEGATED_DISAGREEMENT = re.compile(r"反対(?:では|じゃ)(?:ありません|ない)|反対し(?:ません|ない)", re.I)


def classify_reaction(text):
    """Return a conservative label and excerpt.

    Explicitly negated disagreement is treated as agreement only when an
    affirmative cue is also present. Otherwise it stays unclassified rather
    than inventing sentiment.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[。!?！？])|\n+", text) if s.strip()]
    for sentence in sentences:
        if NEGATED_DISAGREEMENT.search(sentence):
            if re.search(REACTION_RULES[-1][1], sentence, re.I):
                return "同意", sentence[:400]
            continue
        for category, rule in REACTION_RULES:
            if re.search(rule, sentence, re.I):
                return category, sentence[:400]
    return "未分類", text[:400]


def reactions(posts):
    counts = {name: 0 for name, _ in REACTION_RULES}
    counts["未分類"] = 0
    evidence = []
    for p in posts:
        label, excerpt = classify_reaction(p["text"])
        counts[label] += 1
        evidence.append({"post_id": p["id"], "kind": p["kind"], "category": label, "excerpt": excerpt,
                         "created_at": p["created_at"], "observed_at": p["snapshots"][-1]["observed_at"],
                         "needs_review": True})
    return {"method": "語句による保守的な仮分類・人の確認が必要", "sample_size": len(posts), "counts": counts,
            "evidence": evidence, "note": "取得した返信・引用の中での傾向です。全返信・閲覧者全体・世論の割合ではありません。否定・皮肉・複文は曖昧な場合に未分類へ残します。"}


def hypotheses(comparison, reaction, features):
    items = []
    for feature in comparison.get("features", []):
        if feature["relation"] == "higher":
            items.append({"kind": "仮説", "text": f"「{feature['name']}」が高反応群に多く見られました。内容の理解・共有を助けた可能性があります。",
                          "evidence_ids": feature["high_ids"],
                          "alternative": "投稿者の読者層、発信時刻、外部での紹介、広告などでも差は生じます。構成の因果効果は未検証。"})
    if not items:
        items.append({"kind": "判定不能", "text": "構成の違いから伸びた理由を判断できる比較データが足りません。",
                      "evidence_ids": [], "alternative": "同条件の通常投稿と、異なる反応水準の投稿を追加で観測してください。"})
    angles = []
    for e in reaction["evidence"]:
        if e["category"] in {"質問", "訂正", "反論"}:
            angles.append({"type": "調べる論点", "text": e["excerpt"], "evidence_id": e["post_id"],
                           "next_step": "既存の回答・一次資料を確認し、自分の経験や実証で追加できる部分を検討する。未回答かどうかは未確認。"})
    if not angles:
        angles.append({"type": "次の検証", "text": "この主張を確かめる実例・反例を集める", "evidence_id": None,
                       "next_step": "実際の反応を取得すると、読者が疑問に感じた箇所を確認できます。"})
    return {"hypotheses": items[:3], "angles": angles[:5]}
