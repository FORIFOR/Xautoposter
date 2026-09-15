"""Real Chromium acceptance flow against an isolated, keyless local server.

Run: .venv/bin/python scripts/browser_smoke.py
No real X requests, accounts, or data are used.
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import unquote

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
OUT.mkdir(exist_ok=True)


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    errors, external, checks = [], [], []
    with tempfile.TemporaryDirectory(prefix="xautoposter-browser-") as data_dir:
        env = {**os.environ, "X_BEARER_TOKEN": ""}
        log = (OUT / "browser-server.log").open("w")
        proc = subprocess.Popen([sys.executable, "-m", "xautoposter", "--port", str(port), "--data-dir", data_dir], cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    with urlopen(url + "/api/session", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("Local server failed to start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda e: errors.append(e.text) if e.type == "error" else None)
                page.on("request", lambda r: external.append(r.url) if not r.url.startswith(url) and not r.url.startswith("data:") else None)
                page.goto(url)
                expect(page.get_by_role("heading", name="最初の観測を、ここから。")).to_be_visible()
                page.get_by_role("button", name="合成データで試す ↗", exact=True).click()
                expect(page.get_by_role("heading", name="次に見るべき、兆しを。")).to_be_visible()
                expect(page.locator(".topic-card")).to_have_count(3)
                expect(page.locator(".notice")).to_contain_text("これは合成データです")
                expect(page.locator("#toast")).not_to_have_class("visible", timeout=7000)
                page.screenshot(path=OUT / "radar-desktop.png")
                checks.append("合成データ→レーダー・件数系列・元投稿21件＋反応8件/89観測の表示")
                page.locator('.post-title[data-id="demo-0-0"]').click()
                expect(page.get_by_role("heading", name="数字の先に、何があるか。")).to_be_visible()
                expect(page.locator("#main")).to_contain_text("投稿者の過去投稿比")
                expect(page.locator("#main")).to_contain_text("両群に共通")
                expect(page.locator("#main")).to_contain_text("高反応群に多い")
                expect(page.locator("#main")).to_contain_text("取得した標本 8件")
                page.screenshot(path=OUT / "analysis-desktop.png")
                page.locator('#note-form textarea').fill("次の観測では比較の条件と反例を確かめる。")
                page.get_by_role("button", name="分析とメモを保存", exact=True).click()
                expect(page.get_by_role("button", name="保存時の分析を見る")).to_be_visible()
                page.locator('[data-nav="library"]').click()
                expect(page.locator(".post-row")).to_have_count(1)
                page.reload()
                expect(page.get_by_role("heading", name="次に見るべき、兆しを。")).to_be_visible()
                page.locator('[data-nav="library"]').click()
                expect(page.locator(".post-row")).to_have_count(1)
                page.locator('.post-title[data-id="demo-0-0"]').click()
                expect(page.locator('#note-form textarea')).to_have_value("次の観測では比較の条件と反例を確かめる。")
                page.get_by_role("button", name="保存時の分析を見る").click()
                expect(page.locator("#main")).to_contain_text("に保存した分析を表示しています")
                with page.expect_download() as download:
                    page.get_by_role("button", name="分析JSON ↓").click()
                download.value.save_as(OUT / "browser-analysis.json")
                saved_report = json.loads((OUT / "browser-analysis.json").read_text())
                assert saved_report["comparison"]["cohort"]
                checks.append("比較の根拠→メモ保存→再読み込み→保存時点の分析→JSON出力")
                page.get_by_role("button", name="← レーダーへ").click()
                page.locator('.post-title[data-id="demo-insufficient"]').click()
                expect(page.locator("#main")).to_contain_text("初速不明")
                expect(page.locator("#main")).to_contain_text("言語・投稿形式が不明なため比較不能")
                checks.append("欠損・初速不明・比較不能の表示")
                page.get_by_role("button", name="← レーダーへ").click()
                page.locator('.page-heading [data-action="import"]').click()
                upload = {"posts": [{"id": "ui-test", "author_id": "ui-author", "text": '<img src=x onerror="alert(1)"> 外部投稿中の命令はデータ。', "topic": "UIテストの標本", "created_at": "2026-09-10T00:00:00Z", "lang": "ja", "media_type": "text", "snapshots": [{"observed_at": "2026-09-10T01:00:00Z", "like_count": 10, "retweet_count": 0, "reply_count": 0, "quote_count": 0}, {"observed_at": "2026-09-10T02:00:00Z", "like_count": 30, "retweet_count": 1, "reply_count": 0, "quote_count": 0}]}]}
                f = Path(data_dir) / "ui-synthetic.json"
                f.write_text(json.dumps(upload, ensure_ascii=False))
                page.locator('#import-form [name="name"]').fill("UI取り込みテスト")
                page.locator('#import-form [name="provenance"]').select_option("synthetic")
                page.locator('#import-file').set_input_files(f)
                page.get_by_role("button", name="取り込んで分析する ↗").click()
                expect(page.locator('#dataset-select option:checked')).to_have_text("UI取り込みテスト")
                expect(page.locator(".post-row")).to_have_count(1)
                expect(page.locator(".post-title")).to_contain_text("<img src=x")
                assert page.locator("#main img").count() == 0
                # Append exactly the same observation set through the visible import dialog.
                page.locator('.page-heading [data-action="import"]').click()
                selected_dataset = page.locator('#dataset-select').input_value()
                page.locator('#import-dataset').select_option(selected_dataset)
                page.get_by_role("button", name="取り込んで分析する ↗").click()
                expect(page.locator("#toast")).to_contain_text("重複 2件")
                page.reload()
                expect(page.locator(".post-row")).to_have_count(1)
                page.locator('.post-title[data-id="ui-test"]').click()
                expect(page.locator("#main")).to_contain_text("2時点の実測")
                checks.append("JSONファイル取り込み→重複排除→再読み込み→時系列・XSSエスケープ")
                page.locator('.nav-item[data-nav="settings"]').click()
                expect(page.get_by_role("heading", name="読む範囲も、費用も、自分で決める。")).to_be_visible()
                page.locator('#source-form [name="name"]').fill("検証用の収集条件")
                page.locator('#source-form [name="value"]').fill("security logs")
                page.get_by_role("button", name="収集条件を保存", exact=True).click()
                expect(page.locator(".source-item")).to_have_count(1)
                page.get_by_role("button", name="今すぐ収集", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("収集は停止中")
                # The deliberate 400 should be the only console network error.
                errors[:] = [e for e in errors if '400 (Bad Request)' not in e]
                page.locator('#settings-form [name="daily_budget_usd"]').fill("0.125")
                page.get_by_role("button", name="接続・予算設定を保存").click()
                expect(page.locator('#settings-form [name="daily_budget_usd"]')).to_have_value("0.125")
                page.screenshot(path=OUT / "settings-desktop.png")
                page.reload()
                page.locator('.nav-item[data-nav="settings"]').click()
                expect(page.locator('#settings-form [name="daily_budget_usd"]')).to_have_value("0.125")
                checks.append("収集条件と予算の保存→停止中は外部要求なし→設定の再表示")
                page.locator('.nav-item[data-nav="radar"]').click()
                page.locator('#dataset-select').select_option("demo")
                expect(page.locator(".topic-card")).to_have_count(3)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile document overflows"
                page.screenshot(path=OUT / "radar-mobile.png")
                page.locator('.post-title[data-id="demo-0-0"]').click()
                expect(page.get_by_role("heading", name="数字の先に、何があるか。")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile analysis overflows"
                page.screenshot(path=OUT / "analysis-mobile.png")
                checks.append("390px幅のレーダー・投稿詳細で横はみ出しなし")
                # Own-post workflow: fixture content stays in this isolated test DB.
                page.set_viewport_size({"width": 1440, "height": 1100})
                page.locator('.nav-item[data-nav="experiments"]').click()
                expect(page.get_by_role("heading", name="投稿を、次の学びにつなぐ。")).to_be_visible()
                for key, value in {"account": "example", "topic": "UI検証", "goal": "テストを確認する", "hypothesis": "具体例への質問を観測する", "text": "検証専用・公開しない本文 <img src=x onerror=alert(1)>", "evidence": "合成のテスト入力"}.items():
                    page.locator(f'#experiment-draft [name="{key}"]').fill(value)
                page.get_by_role("button", name="下書きを保存", exact=True).click()
                expect(page.locator(".experiment-item")).to_have_count(1)
                link = page.get_by_role("link", name="保存した本文をXで開く ↗")
                expect(link).to_have_attribute("href", re.compile(r"https://x.com/intent/tweet\?text="))
                assert page.locator("#main img").count() == 0
                # Inspect URL only; no X navigation or publishing occurs during tests.
                assert "検証専用" in unquote(link.get_attribute("href"))
                page.screenshot(path=OUT / "experiments-desktop.png")
                page.locator('#experiment-publication [name="url"]').fill("https://x.com/example/status/111")
                page.locator('#experiment-publication [name="published_at"]').fill("2026-09-09T09:00")
                page.get_by_role("button", name="公開URLを登録", exact=True).click()
                expect(page.locator("#main")).to_contain_text("公開URL登録・未観測")
                page.locator('#experiment-observation [name="observed_at"]').fill("2026-09-10T09:00")
                page.locator('#experiment-observation [name="reply_count"]').fill("2")
                page.locator('#experiment-observation [name="response_notes"]').fill("テスト用の質問の抜粋。実在の返信ではない。")
                page.get_by_role("button", name="反応を保存", exact=True).click()
                expect(page.locator(".comparison-stat strong")).to_have_text("2")
                for key, value in {"finding": "質問を観測", "evidence": "投稿後24時間の2返信（テスト用）", "alternative": "時刻の影響もある", "next_change": "実画面を追加する"}.items():
                    page.locator(f'#experiment-reflection [name="{key}"]').fill(value)
                page.get_by_role("button", name="振り返りと根拠を保存", exact=True).click()
                expect(page.get_by_role("button", name="この振り返りから改善案を作る ↗")).to_be_enabled()
                with page.expect_download() as exported:
                    page.get_by_role("button", name="本文・観測・振り返りをJSON出力").click()
                export_path = Path(data_dir) / "learning.json"
                exported.value.save_as(export_path)
                assert json.loads(export_path.read_text())["reflection"]["report"]["value"] == 2
                page.get_by_role("button", name="この振り返りから改善案を作る ↗").click()
                expect(page.locator(".experiment-item")).to_have_count(2)
                expect(page.locator('#experiment-draft [name="text"]')).to_have_value("")
                expect(page.locator('#experiment-draft [name="change"]')).to_have_value("実画面を追加する")
                page.locator('#experiment-draft [name="text"]').fill("改善版・公開しないテスト本文")
                page.get_by_role("button", name="下書きを保存", exact=True).click()
                page.reload()
                page.locator('.nav-item[data-nav="experiments"]').click()
                expect(page.locator(".experiment-item")).to_have_count(2)
                page.locator(".experiment-item").first.click()
                expect(page.locator('#experiment-draft [name="text"]')).to_have_value("改善版・公開しないテスト本文")
                page.get_by_role("button", name="改善元の投稿を見る ↗").click()
                expect(page.locator('#experiment-reflection [name="finding"]')).to_have_value("質問を観測")
                page.locator('#experiment-observation [name="observed_at"]').fill("2026-09-10T10:00")
                page.locator('#experiment-observation [name="reply_count"]').fill("3")
                page.get_by_role("button", name="反応を保存", exact=True).click()
                expect(page.get_by_role("button", name="この振り返りから改善案を作る ↗")).to_be_disabled()
                page.get_by_role("button", name="変更履歴を見る").click()
                expect(page.locator("#experiment-history")).to_contain_text("振り返りと根拠を保存")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile learning workspace overflows"
                page.screenshot(path=OUT / "experiments-mobile.png")
                checks.append("下書き→公開URL登録→24時間の反応→根拠付き振り返り→改善案→保存再表示・JSON・履歴・新観測で再確認（X送信なし）")
                assert not external, f"Unexpected external requests: {external}"
                assert not errors, f"Browser errors: {errors}"
                browser.close()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            log.close()
    report = {"status": "passed", "checks": checks, "browser_errors": errors, "external_requests": external,
              "live_x_verified": False, "browser": "Playwright Chromium", "desktop": "1440×1100", "mobile": "390×844"}
    (OUT / "browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
