import os
import re
import requests
from datetime import date, datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

START_URL = (
    "https://license-renew.tokyo-madoguchi-yoyaku.com/"
    "police-pref-tokyo/index_000.html"
)

# 10/14が現在の最短枠なので、それより前だけ探す
CUTOFF = date(2026, 10, 14)

# 3会場すべて監視
LOCATIONS = [
    "府中試験場",
    "鮫洲試験場",
    "江東試験場",
]

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")


def click_text(page, text):
    print("クリック:", text)

    locator = page.get_by_text(text, exact=True)

    if locator.count() == 0:
        raise Exception(f"「{text}」が見つかりません")

    locator.first.click()
    page.wait_for_timeout(1200)


def send_discord(message):
    print("")
    print("=== 通知内容 ===")
    print(message)

    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK未設定のため、Discord通知は送信しません")
        return

    response = requests.post(
        DISCORD_WEBHOOK,
        json={"content": message},
        timeout=20,
    )

    print("Discord status:", response.status_code)


def get_year_month(page):
    text = page.locator("body").inner_text()

    m = re.search(
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月",
        text
    )

    if not m:
        return None, None

    return int(m.group(1)), int(m.group(2))


def setup_to_location(page, location):
    print("")
    print("==============================")
    print("開始:", location)
    print("==============================")

    # 入口
    page.goto(
        START_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(2500)

    print("入口タイトル:", page.title())

    # 学科試験の予約はこちら
    click_text(page, "学科試験の予約はこちら")

    # 利用規約チェック
    checkbox = page.locator('input[type="checkbox"]').first

    if checkbox.count() == 0:
        raise Exception("利用規約のチェックボックスが見つかりません")

    checkbox.check(force=True)
    page.wait_for_timeout(500)

    # 手続開始
    click_text(page, "手続を開始する")

    # 学科試験
    click_text(page, "学科試験")

    # 教習所卒業等
    click_text(page, "教習所卒業等")

    # 免許証のみ
    click_text(page, "免許証のみ")

    # 試験場
    click_text(page, location)

    page.wait_for_timeout(2500)

    body = page.locator("body").inner_text()

    if "日付を選択してください" not in body:
        print(body[:4000])
        raise Exception(
            f"{location}: カレンダー画面まで到達できませんでした"
        )

    year, month = get_year_month(page)

    print(
        f"{location}: カレンダー到達 "
        f"{year}-{month:02d}"
        if year else
        f"{location}: 年月取得失敗"
    )


def click_next_month(page):
    selectors = [
        ".ui-datepicker-next",
        'a[title*="次"]',
        'button[title*="次"]',
        '[aria-label*="次"]',
        'a[class*="next"]',
        'button[class*="next"]',
    ]

    for selector in selectors:
        try:
            el = page.locator(selector).first

            if el.count() > 0 and el.is_visible():
                el.click()
                page.wait_for_timeout(1200)
                return True
        except Exception:
            pass

    # HTMLから next を探す
    for i in range(page.locator("a, button").count()):
        try:
            el = page.locator("a, button").nth(i)

            if not el.is_visible():
                continue

            html = el.evaluate(
                "el => el.outerHTML"
            ).lower()

            if (
                "next" in html
                or "right" in html
            ):
                el.click()
                page.wait_for_timeout(1200)
                return True

        except Exception:
            pass

    return False


def click_previous_month(page):
    selectors = [
        ".ui-datepicker-prev",
        'a[title*="前"]',
        'button[title*="前"]',
        '[aria-label*="前"]',
        'a[class*="prev"]',
        'button[class*="prev"]',
    ]

    for selector in selectors:
        try:
            el = page.locator(selector).first

            if el.count() > 0 and el.is_visible():
                el.click()
                page.wait_for_timeout(1200)
                return True
        except Exception:
            pass

    for i in range(page.locator("a, button").count()):
        try:
            el = page.locator("a, button").nth(i)

            if not el.is_visible():
                continue

            html = el.evaluate(
                "el => el.outerHTML"
            ).lower()

            if (
                "prev" in html
                or "left" in html
            ):
                el.click()
                page.wait_for_timeout(1200)
                return True

        except Exception:
            pass

    return False


def move_to_month(page, target_year, target_month):
    for _ in range(5):
        year, month = get_year_month(page)

        if year is None:
            return False

        current_value = year * 12 + month
        target_value = target_year * 12 + target_month

        if current_value == target_value:
            return True

        if current_value < target_value:
            if not click_next_month(page):
                return False
        else:
            if not click_previous_month(page):
                return False

    return False


def find_clickable_day(page, day):
    """
    薄いグレーの日は除外し、
    実際に選択できる日だけ探す。
    """

    regex = re.compile(rf"^\s*{day}\s*$")

    candidates = page.locator("a, button").filter(
        has_text=regex
    )

    for i in range(candidates.count()):
        try:
            el = candidates.nth(i)

            if not el.is_visible():
                continue

            if not el.is_enabled():
                continue

            aria_disabled = el.get_attribute(
                "aria-disabled"
            )

            cls = (
                el.get_attribute("class") or ""
            ).lower()

            if aria_disabled == "true":
                continue

            if "disabled" in cls:
                continue

            return el

        except Exception:
            pass

    return None


def read_remaining_slots(page):
    """
    選択した日の
    午前・午後の残り人数を読む。
    """

    text = page.locator("body").inner_text()

    results = []

    # 午前
    morning = re.search(
        r"午前試験"
        r".{0,120}?"
        r"受付時間\s*8:00"
        r".{0,120}?"
        r"残り\s*(\d+)\s*名",
        text,
        re.S
    )

    if morning:
        results.append(
            ("午前 8:00", int(morning.group(1)))
        )

    # 午後
    afternoon = re.search(
        r"午後試験"
        r".{0,120}?"
        r"受付時間\s*11:00"
        r".{0,120}?"
        r"残り\s*(\d+)\s*名",
        text,
        re.S
    )

    if afternoon:
        results.append(
            ("午後 11:00", int(afternoon.group(1)))
        )

    return results


def scan_month(page, location, year, month):
    print("")
    print(
        f"--- {location} "
        f"{year}/{month} を確認 ---"
    )

    if not move_to_month(page, year, month):
        print("対象月へ移動できませんでした")
        return []

    today = datetime.now(
        ZoneInfo("Asia/Tokyo")
    ).date()

    open_slots = []

    for day in range(1, 32):
        try:
            d = date(year, month, day)
        except ValueError:
            continue

        # 過去日は無視
        if d < today:
            continue

        # 10/14以降は今回不要
        if d >= CUTOFF:
            continue

        day_element = find_clickable_day(
            page,
            day
        )

        if day_element is None:
            continue

        print(
            f"{location} {d.isoformat()} "
            "選択可能 → 残席確認"
        )

        try:
            day_element.click()
            page.wait_for_timeout(700)

        except Exception as e:
            print(
                "日付クリック失敗:",
                d,
                e
            )
            continue

        slots = read_remaining_slots(page)

        if not slots:
            print("  残席表示を取得できず")
            continue

        for time_name, remaining in slots:
            print(
                f"  {time_name}: "
                f"残り{remaining}名"
            )

            if remaining > 0:
                open_slots.append(
                    {
                        "location": location,
                        "date": d,
                        "time": time_name,
                        "remaining": remaining,
                    }
                )

    return open_slots


def check_location(browser, location):
    context = browser.new_context(
        viewport={
            "width": 1280,
            "height": 1200
        },
        locale="ja-JP",
        user_agent=(
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/128.0.0.0 "
            "Safari/537.36"
        )
    )

    page = context.new_page()

    try:
        setup_to_location(
            page,
            location
        )

        results = []

        # 9月
        results += scan_month(
            page,
            location,
            2026,
            9
        )

        # 10/1〜10/13
        results += scan_month(
            page,
            location,
            2026,
            10
        )

        return results

    except Exception as e:
        print("")
        print(
            f"❌ {location}でエラー:",
            e
        )

        try:
            print(
                page.locator(
                    "body"
                ).inner_text()[:4000]
            )
        except Exception:
            pass

        return []

    finally:
        context.close()


def main():
    all_open_slots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True
        )

        # 3試験場を順番に確認
        for location in LOCATIONS:
            result = check_location(
                browser,
                location
            )

            all_open_slots.extend(
                result
            )

        browser.close()

    print("")
    print("======================")
    print("最終結果")
    print("======================")

    if not all_open_slots:
        print(
            "10/14より前に残席のある枠は"
            "見つかりませんでした"
        )
        return

    lines = [
        "🚨 本免学科試験の早い空きが出ています！",
        ""
    ]

    for slot in all_open_slots:
        d = slot["date"]

        lines.append(
            f"✅ {slot['location']} "
            f"{d.month}/{d.day} "
            f"{slot['time']} "
            f"残り{slot['remaining']}名"
        )

    lines += [
        "",
        "すぐ予約サイトを確認してください。",
        START_URL
    ]

    send_discord(
        "\n".join(lines)
    )


if __name__ == "__main__":
    main()