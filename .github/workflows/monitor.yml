import os
import re
import time
import requests
from datetime import date, datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright


START_URL = (
    "https://license-renew.tokyo-madoguchi-yoyaku.com/"
    "police-pref-tokyo/index_000.html"
)

LATEST_ALERT_DATE = date(2026, 10, 9)

LOCATIONS = [
    "府中試験場",
    "鮫洲試験場",
    "江東試験場",
]

RECHECK_SECONDS = 5

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

# 手動Run workflowのテスト用
TEST_MODE = os.environ.get("TEST_MODE", "false").lower() == "true"
TEST_LOCATION = os.environ.get("TEST_LOCATION", "府中試験場")
TEST_DATE_TEXT = os.environ.get("TEST_DATE", "2026-10-15")


def click_text(page, text):
    print(f"クリック: {text}")

    locator = page.get_by_text(text, exact=True)

    if locator.count() == 0:
        raise Exception(f"「{text}」が見つかりません")

    for i in range(locator.count()):
        el = locator.nth(i)

        try:
            if el.is_visible():
                el.click()
                page.wait_for_timeout(700)
                return
        except Exception:
            pass

    raise Exception(f"「{text}」をクリックできません")


def agree_terms(page):
    print("利用規約に同意します")

    checkbox = page.locator(
        'input[name="TermsOfServiceCheck"]'
    ).first

    if checkbox.count() == 0:
        raise Exception(
            "利用規約チェックボックスが見つかりません"
        )

    checkbox.evaluate("el => el.click()")
    page.wait_for_timeout(300)

    if not checkbox.is_checked():
        raise Exception(
            "利用規約にチェックできませんでした"
        )

    print("利用規約チェック: OK")


def send_discord(message):
    print("")
    print("========== Discord ==========")
    print(message)
    print("=============================")

    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK が未設定です")
        return

    response = requests.post(
        DISCORD_WEBHOOK,
        json={"content": message},
        timeout=20
    )

    print("Discord status:", response.status_code)


def get_year_month(page):
    body = page.locator("body").inner_text()

    match = re.search(
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月",
        body
    )

    if not match:
        return None, None

    return int(match.group(1)), int(match.group(2))


def setup_calendar(page, location):
    print("")
    print("==============================")
    print("開始:", location)
    print("==============================")

    page.goto(
        START_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(1200)

    print("入口タイトル:", page.title())

    click_text(page, "学科試験の予約はこちら")
    agree_terms(page)
    click_text(page, "手続を開始する")
    click_text(page, "空き状況カレンダー")
    click_text(page, "教習所卒業等")
    click_text(page, "免許証のみ")
    click_text(page, location)

    page.wait_for_timeout(1000)

    body = page.locator("body").inner_text()

    if "日付を選択してください" not in body:
        print(body[:3000])

        raise Exception(
            f"{location}: カレンダーまで到達できません"
        )

    year, month = get_year_month(page)

    print(
        f"{location}: カレンダー到達 "
        f"{year}-{month:02d}"
    )


def click_previous_month(page):
    for selector in [
        ".ui-datepicker-prev",
        '[title*="前"]',
        '[aria-label*="前"]',
    ]:
        try:
            el = page.locator(selector).first

            if el.count() > 0 and el.is_visible():
                el.click()
                page.wait_for_timeout(500)
                return True
        except Exception:
            pass

    return False


def click_next_month(page):
    for selector in [
        ".ui-datepicker-next",
        '[title*="次"]',
        '[aria-label*="次"]',
    ]:
        try:
            el = page.locator(selector).first

            if el.count() > 0 and el.is_visible():
                el.click()
                page.wait_for_timeout(500)
                return True
        except Exception:
            pass

    return False


def move_to_month(page, target_year, target_month):
    for _ in range(6):

        year, month = get_year_month(page)

        if year is None:
            return False

        if year == target_year and month == target_month:
            return True

        current = year * 12 + month
        target = target_year * 12 + target_month

        if current < target:
            if not click_next_month(page):
                return False
        else:
            if not click_previous_month(page):
                return False

    return False


def find_day_cell(page, day):
    cells = page.locator(
        ".ui-datepicker-calendar td"
    )

    for i in range(cells.count()):
        try:
            cell = cells.nth(i)

            if cell.inner_text().strip() != str(day):
                continue

            info = cell.evaluate(
                """
                el => ({
                    cls: String(el.className || ""),
                    ariaDisabled: el.getAttribute("aria-disabled"),
                    links: el.querySelectorAll("a").length,
                    buttons: el.querySelectorAll("button").length
                })
                """
            )

            cls = (info["cls"] or "").lower()

            if (
                "ui-datepicker-unselectable" in cls
                or "ui-state-disabled" in cls
                or info["ariaDisabled"] == "true"
            ):
                return None

            if info["links"] == 0 and info["buttons"] == 0:
                return None

            return cell

        except Exception:
            pass

    return None


def read_remaining_slots(page):
    body = page.locator("body").inner_text()

    results = []

    definitions = [
        ("午前試験 8:00", "午前試験", "8:00"),
        ("午後試験 11:00", "午後試験", "11:00"),
    ]

    for label, section, time_text in definitions:

        pattern = (
            re.escape(section)
            + r".{0,300}?"
            + re.escape(time_text)
            + r".{0,300}?"
            + r"残り\s*(\d+)\s*名"
        )

        match = re.search(
            pattern,
            body,
            re.S
        )

        if match:
            results.append(
                {
                    "time": label,
                    "remaining": int(match.group(1))
                }
            )

    return results


def inspect_date(page, target_date):
    if not move_to_month(
        page,
        target_date.year,
        target_date.month
    ):
        print("❌ 対象月へ移動できません")
        return None

    print("")
    print(
        "対象日を実際に確認:",
        target_date.isoformat()
    )

    cell = find_day_cell(
        page,
        target_date.day
    )

    if cell is None:
        print(
            "❌ この日は現在カレンダー上で"
            "選択できません"
        )
        return None

    print("✅ 日付は選択可能です")
    print("→ 実際にクリックします")

    link = cell.locator("a").first

    if link.count() > 0:
        link.click()
    else:
        button = cell.locator("button").first
        button.click()

    page.wait_for_timeout(800)

    print("✅ 日付クリック成功")

    slots = read_remaining_slots(page)

    if not slots:
        print(
            "❌ クリック後の"
            "「残り○名」を取得できません"
        )
        return []

    print("")
    print("========== 残席 ==========")

    for slot in slots:
        print(
            f"{slot['time']} → "
            f"残り{slot['remaining']}名"
        )

    print("==========================")

    return slots


def positive_slots(page, target_date):
    slots = inspect_date(
        page,
        target_date
    )

    if not slots:
        return []

    return [
        {
            "date": target_date,
            "time": slot["time"],
            "remaining": slot["remaining"]
        }
        for slot in slots
        if slot["remaining"] > 0
    ]


def scan_month(page, location, year, month):
    print("")
    print(
        f"===== {location} {year}/{month} ====="
    )

    if not move_to_month(
        page,
        year,
        month
    ):
        raise Exception(
            f"{location}: {year}/{month}へ移動できません"
        )

    today = datetime.now(
        ZoneInfo("Asia/Tokyo")
    ).date()

    results = []

    for day in range(1, 32):

        try:
            target_date = date(
                year,
                month,
                day
            )
        except ValueError:
            continue

        if target_date <= today:
            continue

        if target_date > LATEST_ALERT_DATE:
            continue

        cell = find_day_cell(
            page,
            day
        )

        if cell is None:
            continue

        slots = positive_slots(
            page,
            target_date
        )

        for slot in slots:
            slot["location"] = location
            results.append(slot)

    return results


def create_context(browser):
    return browser.new_context(
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
            "Chrome/128.0.0.0 Safari/537.36"
        )
    )


def scan_location(browser, location):
    context = create_context(browser)
    page = context.new_page()

    try:
        setup_calendar(
            page,
            location
        )

        results = []

        results += scan_month(
            page,
            location,
            2026,
            9
        )

        results += scan_month(
            page,
            location,
            2026,
            10
        )

        return results, None

    except Exception as e:
        print(
            f"❌ {location} エラー:",
            e
        )
        return [], str(e)

    finally:
        context.close()


def recheck_location(
    browser,
    location,
    original_slots
):
    context = create_context(browser)
    page = context.new_page()

    try:
        setup_calendar(
            page,
            location
        )

        confirmed = []

        unique_dates = sorted(
            set(
                slot["date"]
                for slot in original_slots
            )
        )

        for target_date in unique_dates:

            slots = positive_slots(
                page,
                target_date
            )

            for slot in slots:
                slot["location"] = location
                confirmed.append(slot)

        return confirmed

    finally:
        context.close()


def slot_key(slot):
    return (
        slot["location"],
        slot["date"].isoformat(),
        slot["time"]
    )


def slot_line(slot):
    d = slot["date"]

    return (
        f"✅ {slot['location']} "
        f"{d.month}/{d.day} "
        f"{slot['time']} "
        f"残り{slot['remaining']}名"
    )


def run_manual_test(browser):
    print("")
    print("################################")
    print("🧪 手動クリックテスト")
    print("################################")

    target_date = date.fromisoformat(
        TEST_DATE_TEXT
    )

    print("会場:", TEST_LOCATION)
    print("日付:", target_date)

    context = create_context(browser)
    page = context.new_page()

    try:
        setup_calendar(
            page,
            TEST_LOCATION
        )

        slots = inspect_date(
            page,
            target_date
        )

        if slots is None:
            print("")
            print(
                "⚠️ テスト日は現在選択不可です。"
            )
            print(
                "TEST_DATEを、今カレンダーで"
                "浮き出ている日に変更してください。"
            )
            return

        if slots == []:
            print("")
            print(
                "⚠️ 日付クリック自体は成功しましたが、"
                "残席表示の読み取りに失敗しました。"
            )
            return

        print("")
        print("🎉 テスト成功")
        print(
            "カレンダーの日付をクリックし、"
            "残席数まで取得できています。"
        )

        lines = [
            "🧪 本免監視クリックテスト成功",
            "",
            f"会場: {TEST_LOCATION}",
            f"日付: {target_date.month}/{target_date.day}",
        ]

        for slot in slots:
            lines.append(
                f"・{slot['time']} "
                f"残り{slot['remaining']}名"
            )

        send_discord(
            "\n".join(lines)
        )

    finally:
        context.close()


def run_normal_monitor(browser):
    any_success = False

    for location in LOCATIONS:

        slots, error = scan_location(
            browser,
            location
        )

        if error:
            continue

        any_success = True

        if not slots:
            print(
                f"{location}: 空きなし"
            )
            continue

        lines = [
            "🚨【速報】本免学科試験の空きを検知！",
            ""
        ]

        for slot in slots:
            lines.append(
                slot_line(slot)
            )

        lines += [
            "",
            f"{RECHECK_SECONDS}秒後に再確認します。",
            START_URL
        ]

        send_discord(
            "\n".join(lines)
        )

        time.sleep(
            RECHECK_SECONDS
        )

        confirmed = recheck_location(
            browser,
            location,
            slots
        )

        confirmed_keys = {
            slot_key(slot)
            for slot in confirmed
        }

        vanished = [
            slot
            for slot in slots
            if slot_key(slot)
            not in confirmed_keys
        ]

        if confirmed:

            lines = [
                f"✅【再確認OK】"
                f"{RECHECK_SECONDS}秒後も空いています！",
                ""
            ]

            for slot in confirmed:
                lines.append(
                    slot_line(slot)
                )

            if vanished:
                lines.append("")
                lines.append(
                    "⚠️ 再確認時に消えた枠:"
                )

                for slot in vanished:
                    d = slot["date"]

                    lines.append(
                        f"・{slot['location']} "
                        f"{d.month}/{d.day} "
                        f"{slot['time']}"
                    )

            lines += [
                "",
                "すぐ予約サイトを確認してください。",
                START_URL
            ]

            send_discord(
                "\n".join(lines)
            )

        else:

            send_discord(
                "⚠️【再確認】"
                f"{RECHECK_SECONDS}秒後には"
                "空きがなくなっていました。\n\n"
                "一瞬のキャンセル枠だった可能性があります。\n"
                + START_URL
            )

    if not any_success:
        raise Exception(
            "全会場の監視に失敗しました"
        )


def main():
    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        try:
            if TEST_MODE:
                run_manual_test(
                    browser
                )
            else:
                run_normal_monitor(
                    browser
                )

        finally:
            browser.close()


if __name__ == "__main__":
    main()