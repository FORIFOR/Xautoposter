"""Untrusted input is parsed as data; imports have no network or execution path."""
from __future__ import annotations

import csv
import io
import json

from .models import ImportBundle, METRICS


def parse_import(text: str, format: str, name: str, provenance: str):
    if len(text.encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("取り込みファイルは4MB以下にしてください")
    if format == "json":
        data = json.loads(text)
        if isinstance(data, list):
            data = {"posts": data}
        if not isinstance(data, dict):
            raise ValueError("JSONはposts配列を含むオブジェクトにしてください")
        if "provenance" in data and data["provenance"] != provenance:
            raise ValueError("ファイル内のデータ種別と画面の選択が異なります")
        data["name"] = name
        data["provenance"] = provenance
        return ImportBundle.model_validate(data)
    if format != "csv":
        raise ValueError("対応形式はCSVまたはJSONです")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    required = {"id", "author_id", "text", "created_at", "observed_at", "topic"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("CSVの必須列: " + ", ".join(sorted(required)))
    grouped = {}
    for index, row in enumerate(reader, 2):
        if index > 20001:
            raise ValueError("CSVは20,000行までです")
        if None in row:
            raise ValueError(f"CSV {index}行目: 列数が一致しません")
        snapshot = {"observed_at": row["observed_at"]}
        for key in METRICS:
            val = (row.get(key) or "").strip()
            try:
                snapshot[key] = int(val) if val.lower() not in {"", "null", "na", "n/a"} else None
            except ValueError:
                raise ValueError(f"CSV {index}行目: {key} は非負の整数または空欄にしてください") from None
        body = {key: row[key] for key in ("id", "author_id", "author_name", "text", "created_at", "topic", "lang", "media_type", "kind", "parent_id", "source_query") if row.get(key)}
        key = (row["id"], row["topic"])
        if key not in grouped:
            grouped[key] = {**body, "snapshots": []}
        if {k: v for k, v in grouped[key].items() if k != "snapshots"} != body:
            raise ValueError(f"CSV {index}行目: 同じ投稿IDの本文・属性が一致しません")
        grouped[key]["snapshots"].append(snapshot)
    return ImportBundle.model_validate({"name": name, "provenance": provenance, "posts": list(grouped.values()), "scope": "CSVから取り込んだ標本。話題全体の件数は含みません。"})
