import os
import re
import requests
from datetime import date
from playwright.sync_api import sync_playwright

URL = "https://license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/calendar/01/html/main.html?lang=ja"

# 現時点の最短空き日
CUTOFF = date(2026, 10, 14)

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")


def notify(message):
    print(message)

    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK が未設定です")
        return

    r = requests.post(
        DISCORD_WEBHOOK,
        json={"content": message},
        timeout=20
    )
    print("Discord:", r.status_code)


def scan_month(page):
    # 表示中の「2026年 10月」などを取得
    body = page.locator("body").inner_text()

    m = re.search(r"(20\d{2})年\s*(\d{1,2})月", body)
    if not m:
        print("年月を取得できませんでした")
        return []

    year = int(m.group(1))
    month = int(m.group(2))

    print(f"調査中: {year}-{month:02d}")

    found = []

    # 1～31の日付表示を全部調査
    for day in range(1, 32):
        locators = page.get_by_text(str(day), exact=True)

        for i in range(locators.count()):
            try:
                el = locators.nth(i)

                if not el.is_visible():
                    continue

                # 要素そのもの＋親要素から予約可能か推定
                info = el.evaluate("""
                el => {
                    const p = el.parentElement;

                    return {
                        tag: el.tagName,
                        cls: el.className || "",
                        disabled: el.disabled === true,
                        ariaDisabled: el.getAttribute("aria-disabled"),
                        parentClass: p ? (p.className || "") : "",
                        parentDisabled: p ? p.disabled === true : false,
                        parentAriaDisabled: p ? p.getAttribute("aria-disabled") : null
                    };
                }
                """)

                text = str(info).lower()

                # disabled と明示されているものは除外
                if (
                    info["disabled"]
                    or info["parentDisabled"]
                    or info["ariaDisabled"] == "true"
                    or info["parentAriaDisabled"] == "true"
                    or "disabled" in text
                ):
                    continue

                # 実際にクリック可能か確認
                try:
                    clickable = el.evaluate("""
                    el => {
                        const style = window.getComputedStyle(el);
                        const p = el.parentElement;
                        const ps = p ? window.getComputedStyle(p) : null;

                        return (
                            style.pointerEvents !== "none" &&
                            (!ps || ps.pointerEvents !== "none")
                        );
                    }
                    """)
                except:
                    clickable = False

                if not clickable:
                    continue

                try:
                    d = date(year, month, day)
                except ValueError:
                    continue

                found.append(d)

            except Exception:
                pass

    return sorted(set(found))


def previous_month(page):
    """
    カレンダー左上の「前月」矢印を押す。
    """
    candidates = [
        'button[aria-label*="前"]',
        'a[aria-label*="前"]',
        '.ui-datepicker-prev',
        '[title*="前"]'
    ]

    for selector in candidates:
        try:
            el = page.locator(selector).first
            if el.count() and el.is_visible():
                el.click()
                page.wait_for_timeout(1500)
                return True
        except:
            pass

    # 最後の手段：2026年○月の近くにある左側のクリック要素
    try:
        page.locator("button").filter(has_text="").first.click()
        page.wait_for_timeout(1500)
        return True
    except:
        return False


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={"width": 1280, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            )
        )

        print("予約サイトを開きます")

        page.goto(
            URL,
            wait_until="networkidle",
            timeout=60000
        )

        page.wait_for_timeout(4000)

        print("タイトル:", page.title())

        all_available = []

        # 現在表示されている月
        all_available += scan_month(page)

        # 前月もチェック（9月のキャンセル対策）
        if previous_month(page):
            all_available += scan_month(page)

        browser.close()

    all_available = sorted(set(all_available))

    print("予約可能と判定した日:")
    for d in all_available:
        print(d.isoformat())

    earlier = [d for d in all_available if d < CUTOFF]

    if earlier:
        dates = "\n".join(
            f"✅ {d.month}/{d.day}" for d in earlier
        )

        notify(
            "🚨 本免試験、10/14より前に空きが出た可能性あり！\n\n"
            + dates
            + "\n\nすぐ予約サイトを確認して！\n"
            + URL
        )
    else:
        print("10/14より前の空きはありません")


if __name__ == "__main__":
    main()