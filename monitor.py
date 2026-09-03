import os

import re

import json

import requests

from datetime import datetime

from playwright.sync_api import sync_playwright

URL = "https://license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/calendar/01/html/main.html?lang=ja"

STATE_FILE = "state.json"

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

def send_discord(message):

    if not DISCORD_WEBHOOK:

        print("DISCORD_WEBHOOK が設定されていません")

        return

    response = requests.post(

        DISCORD_WEBHOOK,

        json={"content": message},

        timeout=20

    )

    if response.status_code not in (200, 204):

        print("Discord通知失敗:", response.status_code, response.text)

def load_state():

    if not os.path.exists(STATE_FILE):

        return None

    try:

        with open(STATE_FILE, "r", encoding="utf-8") as f:

            return json.load(f)

    except Exception:

        return None

def save_state(dates):

    data = {

        "available_dates": sorted(dates),

        "updated_at": datetime.now().isoformat()

    }

    with open(STATE_FILE, "w", encoding="utf-8") as f:

        json.dump(data, f, ensure_ascii=False, indent=2)

def normalize_date(text):

    """

    例:

    9月8日

    2026年9月8日

    09/08

    などを 2026-09-08 にする

    """

    # 2026年9月8日

    m = re.search(r"(2026)年\s*(\d{1,2})月\s*(\d{1,2})日", text)

    if m:

        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 9月8日

    m = re.search(r"(\d{1,2})月\s*(\d{1,2})日", text)

    if m:

        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # 9/8

    m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", text)

    if m:

        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    return None

def get_available_dates():

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        page = browser.new_page(

            viewport={"width": 1280, "height": 1000},

            user_agent=(

                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "

                "AppleWebKit/537.36 (KHTML, like Gecko) "

                "Chrome/128.0.0.0 Safari/537.36"

            )

        )

        print("予約サイトを開いています…")

        page.goto(

            URL,

            wait_until="networkidle",

            timeout=60000

        )

        page.wait_for_timeout(5000)

        title = page.title()

        print("ページタイトル:", title)

        body_text = page.locator("body").inner_text()

        print("=== ページ本文の先頭 ===")

        print(body_text[:5000])

        print("=== ここまで ===")

        available_dates = set()

        # 空き枠を表していそうな要素を探す

        elements = page.locator(

            "a, button, td, div, span"

        )

        count = elements.count()

        availability_words = [

            "○",

            "〇",

            "空きあり",

            "予約可",

            "予約可能"

        ]

        for i in range(count):

            try:

                el = elements.nth(i)

                text = el.inner_text(timeout=500).strip()

                if not text:

                    continue

                if not any(word in text for word in availability_words):

                    continue

                # 空き表示の周辺テキストも取得

                surrounding = text

                try:

                    parent_text = el.locator(

                        "xpath=ancestor::*[self::td or self::tr or self::li or self::div][1]"

                    ).inner_text(timeout=500)

                    surrounding += "\n" + parent_text

                except Exception:

                    pass

                date = normalize_date(surrounding)

                if date:

                    available_dates.add(date)

                    print("空き候補:", date, repr(surrounding[:200]))

            except Exception:

                continue

        browser.close()

        return available_dates

def main():

    current_dates = get_available_dates()

    print("現在取得できた空き日:")

    for d in sorted(current_dates):

        print(" -", d)

    previous = load_state()

    # 初回実行

    if previous is None:

        print("初回実行です。現在の状態を基準として保存します。")

        save_state(current_dates)

        return

    previous_dates = set(previous.get("available_dates", []))

    # 前回にはなく、今回新しく出た日

    new_dates = current_dates - previous_dates

    if previous_dates:

        previous_earliest = min(previous_dates)

    else:

        previous_earliest = None

    alert_dates = []

    for date in sorted(new_dates):

        # 2026年9月なら通知

        is_september = date.startswith("2026-09-")

        # 前回の最短日より早ければ通知

        earlier_than_before = (

            previous_earliest is not None

            and date < previous_earliest

        )

        if is_september or earlier_than_before:

            alert_dates.append(date)

    if alert_dates:

        lines = [

            "🚨 本免試験の早い予約枠が出た可能性があります！",

            ""

        ]

        for date in alert_dates:

            lines.append(f"✅ {date}")

        lines += [

            "",

            "すぐ予約サイトを確認してください。",

            URL

        ]

        message = "\n".join(lines)

        print(message)

        send_discord(message)

    else:

        print("通知対象となる新しい空きはありません。")

    save_state(current_dates)

if __name__ == "__main__":

    main()