import os
import re
import time
import requests
from datetime import date, datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright


# ============================================================
# 設定
# ============================================================

START_URL = (
    "https://license-renew.tokyo-madoguchi-yoyaku.com/"
    "police-pref-tokyo/index_000.html"
)

# この日まで通知対象
# 10/9を含む
LATEST_ALERT_DATE = date(2026, 10, 9)

# 監視対象
LOCATIONS = [
    "府中試験場",
    "鮫洲試験場",
    "江東試験場",
]

# 速報後の再確認までの秒数
RECHECK_SECONDS = 5

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")


# ============================================================
# 基本操作
# ============================================================

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

    # inputが非表示なのでJS経由
    checkbox.evaluate("el => el.click()")

    page.wait_for_timeout(300)

    if not checkbox.is_checked():
        raise Exception(
            "利用規約にチェックできませんでした"
        )

    print("利用規約チェック: OK")


# ============================================================
# Discord
# ============================================================

def send_discord(message):
    print("")
    print("========== Discord ==========")
    print(message)
    print("=============================")

    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK が未設定です")
        return

    try:
        response = requests.post(
            DISCORD_WEBHOOK,
            json={"content": message},
            timeout=20
        )

        print("Discord status:", response.status_code)

        if response.status_code not in (200, 204):
            print(response.text)

    except Exception as e:
        print("Discord送信エラー:", e)


# ============================================================
# カレンダー
# ============================================================

def get_year_month(page):
    body = page.locator("body").inner_text()

    match = re.search(
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月",
        body
    )

    if not match:
        return None, None

    return (
        int(match.group(1)),
        int(match.group(2))
    )


def setup_calendar(page, location):
    print("")
    print("================================")
    print(f"開始: {location}")
    print("================================")

    page.goto(
        START_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(1200)

    print("入口タイトル:", page.title())

    click_text(
        page,
        "学科試験の予約はこちら"
    )

    agree_terms(page)

    click_text(
        page,
        "手続を開始する"
    )

    click_text(
        page,
        "空き状況カレンダー"
    )

    click_text(
        page,
        "教習所卒業等"
    )

    click_text(
        page,
        "免許証のみ"
    )

    click_text(
        page,
        location
    )

    page.wait_for_timeout(1000)

    body = page.locator("body").inner_text()

    if "日付を選択してください" not in body:
        print(body[:3000])

        raise Exception(
            f"{location}: "
            "空き状況カレンダーまで到達できません"
        )

    year, month = get_year_month(page)

    if year is None:
        raise Exception(
            f"{location}: 年月を取得できません"
        )

    print(
        f"{location}: "
        f"カレンダー到達 {year}-{month:02d}"
    )


def click_previous_month(page):
    selectors = [
        ".ui-datepicker-prev",
        '[title*="前"]',
        '[aria-label*="前"]',
    ]

    for selector in selectors:
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
    selectors = [
        ".ui-datepicker-next",
        '[title*="次"]',
        '[aria-label*="次"]',
    ]

    for selector in selectors:
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

        if (
            year == target_year
            and month == target_month
        ):
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


# ============================================================
# 日付がクリック可能か確認
# ============================================================

def find_day_cell(page, day):
    cells = page.locator(
        ".ui-datepicker-calendar td"
    )

    for i in range(cells.count()):
        try:
            cell = cells.nth(i)

            text = cell.inner_text().strip()

            if text != str(day):
                continue

            info = cell.evaluate(
                """
                el => ({
                    cls: String(el.className || ""),
                    ariaDisabled:
                        el.getAttribute("aria-disabled"),
                    links:
                        el.querySelectorAll("a").length,
                    buttons:
                        el.querySelectorAll("button").length
                })
                """
            )

            cls = (
                info["cls"]
                or ""
            ).lower()

            if (
                "ui-datepicker-unselectable" in cls
                or
                "ui-state-disabled" in cls
                or
                info["ariaDisabled"] == "true"
            ):
                return None

            if (
                info["links"] == 0
                and info["buttons"] == 0
            ):
                return None

            return cell

        except Exception:
            pass

    return None


# ============================================================
# 「残り○名」を取得
# ============================================================

def read_remaining_slots(page):
    body = page.locator("body").inner_text()

    results = []

    definitions = [
        (
            "午前試験 8:00",
            "午前試験",
            "8:00"
        ),
        (
            "午後試験 11:00",
            "午後試験",
            "11:00"
        ),
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
            remaining = int(
                match.group(1)
            )

            results.append(
                {
                    "time": label,
                    "remaining": remaining
                }
            )

    return results


# ============================================================
# 1日だけ本当に空いているか確認
# ============================================================

def check_date(page, target_date):
    if not move_to_month(
        page,
        target_date.year,
        target_date.month
    ):
        return []

    cell = find_day_cell(
        page,
        target_date.day
    )

    if cell is None:
        return []

    print(
        f"選択可能日を確認: "
        f"{target_date.isoformat()}"
    )

    try:
        link = cell.locator("a").first

        if link.count() > 0:
            link.click()
        else:
            button = cell.locator(
                "button"
            ).first

            button.click()

    except Exception as e:
        print(
            "日付クリック失敗:",
            target_date,
            e
        )
        return []

    page.wait_for_timeout(600)

    slots = read_remaining_slots(page)

    positive = []

    if not slots:
        print(
            "  残席情報を取得できません"
        )
        return []

    for slot in slots:
        print(
            f"  {slot['time']} "
            f"残り{slot['remaining']}名"
        )

        # ★ ここが重要
        # 日付が押せるだけでは通知しない。
        # 実際に残り1名以上だけを空き扱い。
        if slot["remaining"] > 0:
            positive.append(
                {
                    "date": target_date,
                    "time": slot["time"],
                    "remaining": slot["remaining"]
                }
            )

    return positive


# ============================================================
# 9月・10月を調査
# ============================================================

def scan_month(
    page,
    location,
    year,
    month
):
    print("")
    print(
        f"===== {location} "
        f"{year}/{month} ====="
    )

    if not move_to_month(
        page,
        year,
        month
    ):
        raise Exception(
            f"{location}: "
            f"{year}/{month}へ移動できません"
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

        # 今日以前は除外
        if target_date <= today:
            continue

        # 10/9より後は除外
        if target_date > LATEST_ALERT_DATE:
            continue

        cell = find_day_cell(
            page,
            day
        )

        if cell is None:
            continue

        slots = check_date(
            page,
            target_date
        )

        for slot in slots:
            slot["location"] = location
            results.append(slot)

    return results


# ============================================================
# 会場を最初から確認
# ============================================================

def scan_location(
    browser,
    location
):
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


# ============================================================
# 5秒後の再確認
# ============================================================

def recheck_location(
    browser,
    location,
    original_slots
):
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
        setup_calendar(
            page,
            location
        )

        unique_dates = sorted(
            set(
                slot["date"]
                for slot in original_slots
            )
        )

        confirmed = []

        for target_date in unique_dates:

            slots = check_date(
                page,
                target_date
            )

            for slot in slots:
                slot["location"] = location
                confirmed.append(slot)

        return confirmed, None

    except Exception as e:
        return [], str(e)

    finally:
        context.close()


# ============================================================
# 表示用
# ============================================================

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


# ============================================================
# メイン
# ============================================================

def main():
    errors = []
    any_success = False
    any_slot = False

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        for location in LOCATIONS:

            slots, error = scan_location(
                browser,
                location
            )

            if error:
                errors.append(
                    f"{location}: {error}"
                )
                continue

            any_success = True

            if not slots:
                print(
                    f"{location}: "
                    "通知対象の空きなし"
                )
                continue

            any_slot = True

            # --------------------------------------------
            # ① 即時速報
            # --------------------------------------------

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
                f"{RECHECK_SECONDS}秒後に"
                "自動で再確認します。",
                START_URL
            ]

            send_discord(
                "\n".join(lines)
            )

            # --------------------------------------------
            # ② 5秒待つ
            # --------------------------------------------

            print(
                f"{RECHECK_SECONDS}秒待って"
                "再確認します..."
            )

            time.sleep(
                RECHECK_SECONDS
            )

            # --------------------------------------------
            # ③ 新しいブラウザ状態で再確認
            # --------------------------------------------

            confirmed, recheck_error = (
                recheck_location(
                    browser,
                    location,
                    slots
                )
            )

            if recheck_error:

                send_discord(
                    "⚠️ 空き速報後の再確認に失敗しました。\n"
                    f"{location}\n"
                    "最初の検知は成功しているので、"
                    "念のため予約サイトを確認してください。\n"
                    + START_URL
                )

                continue

            original_keys = {
                slot_key(slot)
                for slot in slots
            }

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

            # --------------------------------------------
            # ④ 再確認結果通知
            # --------------------------------------------

            if confirmed:

                lines = [
                    "✅【再確認OK】"
                    f"{RECHECK_SECONDS}秒後も空いています！",
                    ""
                ]

                for slot in confirmed:
                    lines.append(
                        slot_line(slot)
                    )

                if vanished:
                    lines += [
                        "",
                        "⚠️ 次の枠は再確認時には"
                        "消えていました:"
                    ]

                    for slot in vanished:
                        d = slot["date"]

                        lines.append(
                            f"・{slot['location']} "
                            f"{d.month}/{d.day} "
                            f"{slot['time']}"
                        )

                lines += [
                    "",
                    "すぐ予約してください。",
                    START_URL
                ]

                send_discord(
                    "\n".join(lines)
                )

            else:

                lines = [
                    "⚠️【再確認】"
                    f"{RECHECK_SECONDS}秒後には"
                    "空きがなくなっていました。",
                    "",
                    "一瞬のキャンセル枠だったか、"
                    "サイト表示のタイミング差の"
                    "可能性があります。",
                    "",
                    START_URL
                ]

                send_discord(
                    "\n".join(lines)
                )

        browser.close()

    print("")
    print("======================")
    print("最終結果")
    print("======================")

    if not any_success:
        for error in errors:
            print(error)

        raise Exception(
            "全会場の監視に失敗しました"
        )

    if not any_slot:
        print(
            "✅ 正常に監視完了。"
            "現在、明日〜10/9に"
            "残席のある枠はありません。"
        )


if __name__ == "__main__":
    main()