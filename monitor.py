import os
import re
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

# この日までを通知対象にする
# 10/9も含む
LATEST_ALERT_DATE = date(2026, 10, 9)

# 3会場すべて監視
LOCATIONS = [
    "府中試験場",
    "鮫洲試験場",
    "江東試験場",
]

# GitHub Actions の Secret から取得
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")


# ============================================================
# ページ操作
# ============================================================

def click_text(page, text):
    print(f"クリック: {text}")

    locator = page.get_by_text(text, exact=True)

    if locator.count() == 0:
        raise Exception(f"「{text}」が見つかりません")

    for i in range(locator.count()):
        element = locator.nth(i)

        try:
            if element.is_visible():
                element.click()
                page.wait_for_timeout(800)
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
            "利用規約のチェックボックスが見つかりません"
        )

    # このサイトでは実際のinputが非表示なので
    # JavaScript経由でクリック
    checkbox.evaluate("el => el.click()")

    page.wait_for_timeout(400)

    if not checkbox.is_checked():
        raise Exception(
            "利用規約にチェックできませんでした"
        )

    print("利用規約チェック: OK")


# ============================================================
# Discord通知
# ============================================================

def send_discord(message):
    print("")
    print("========== 通知内容 ==========")
    print(message)
    print("==============================")

    if not DISCORD_WEBHOOK:
        print(
            "DISCORD_WEBHOOK が未設定です。"
            "Discord通知は送信されません。"
        )
        return

    try:
        response = requests.post(
            DISCORD_WEBHOOK,
            json={"content": message},
            timeout=20
        )

        print(
            "Discord status:",
            response.status_code
        )

        if response.status_code not in (200, 204):
            print(
                "Discord通知エラー:",
                response.text
            )

    except Exception as e:
        print(
            "Discord通知送信失敗:",
            e
        )


# ============================================================
# カレンダー関係
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

    page.wait_for_timeout(1500)

    print(
        "入口タイトル:",
        page.title()
    )

    # ① 学科試験
    click_text(
        page,
        "学科試験の予約はこちら"
    )

    # ② 利用規約
    agree_terms(page)

    # ③ 手続開始
    click_text(
        page,
        "手続を開始する"
    )

    # ④ 実予約ではなく空き状況カレンダー
    click_text(
        page,
        "空き状況カレンダー"
    )

    # ⑤ 教習所卒業
    click_text(
        page,
        "教習所卒業等"
    )

    # ⑥ 従来の免許証
    click_text(
        page,
        "免許証のみ"
    )

    # ⑦ 会場
    click_text(
        page,
        location
    )

    page.wait_for_timeout(1200)

    body = page.locator(
        "body"
    ).inner_text()

    if "日付を選択してください" not in body:
        print(body[:3000])

        raise Exception(
            f"{location}: "
            "空き状況カレンダーまで到達できません"
        )

    year, month = get_year_month(page)

    if year is None:
        raise Exception(
            f"{location}: "
            "カレンダーの年月を取得できません"
        )

    print(
        f"{location}: "
        f"カレンダー到達 "
        f"{year}-{month:02d}"
    )


def click_previous_month(page):
    selectors = [
        ".ui-datepicker-prev",
        '[title*="前"]',
        '[aria-label*="前"]',
    ]

    for selector in selectors:
        try:
            element = page.locator(
                selector
            ).first

            if (
                element.count() > 0
                and element.is_visible()
            ):
                element.click()
                page.wait_for_timeout(600)
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
            element = page.locator(
                selector
            ).first

            if (
                element.count() > 0
                and element.is_visible()
            ):
                element.click()
                page.wait_for_timeout(600)
                return True

        except Exception:
            pass

    return False


def move_to_month(
    page,
    target_year,
    target_month
):
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
# 空き判定
# ============================================================

def get_available_days(
    page,
    year,
    month
):
    available = []

    # 日本時間で「今日」を毎回取得
    today = datetime.now(
        ZoneInfo("Asia/Tokyo")
    ).date()

    print("日本時間の今日:", today.isoformat())

    cells = page.locator(
        ".ui-datepicker-calendar td"
    )

    print(
        "カレンダーセル数:",
        cells.count()
    )

    for i in range(cells.count()):

        try:
            cell = cells.nth(i)

            text = (
                cell.inner_text()
                .strip()
            )

            if not text.isdigit():
                continue

            day = int(text)

            try:
                target_date = date(
                    year,
                    month,
                    day
                )

            except ValueError:
                continue

            # ----------------------------------------
            # 今日以前は監視対象外
            #
            # 9/3に実行 → 9/3以前を除外
            # 9/4に実行 → 9/4以前を除外
            # 9/5に実行 → 9/5以前を除外
            # と自動で変わる
            # ----------------------------------------

            if target_date <= today:
                continue

            # ----------------------------------------
            # 10/9より後は通知対象外
            # 10/9は含む
            # ----------------------------------------

            if target_date > LATEST_ALERT_DATE:
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

            # グレー表示・選択不可の日を除外
            if (
                "ui-datepicker-unselectable" in cls
                or
                "ui-state-disabled" in cls
                or
                info["ariaDisabled"] == "true"
            ):
                continue

            # 実際に選択可能な日は
            # 基本的に a または button が入っている
            clickable = (
                info["links"] > 0
                or
                info["buttons"] > 0
            )

            if not clickable:
                continue

            available.append(
                target_date
            )

            print(
                "🚨 空き候補:",
                target_date.isoformat()
            )

        except Exception as e:
            print(
                "セル確認エラー:",
                e
            )

    return available


def scan_month(
    page,
    location,
    year,
    month
):
    print("")
    print(
        "===== "
        f"{location} "
        f"{year}/{month} "
        "====="
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

    actual_year, actual_month = (
        get_year_month(page)
    )

    print(
        "表示中:",
        actual_year,
        actual_month
    )

    return get_available_days(
        page,
        year,
        month
    )


# ============================================================
# 各試験場
# ============================================================

def check_location(
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

        dates = []

        # 2026年9月
        dates += scan_month(
            page,
            location,
            2026,
            9
        )

        # 2026年10月
        # 10/9までだけ実際に拾う
        dates += scan_month(
            page,
            location,
            2026,
            10
        )

        return dates, None

    except Exception as e:

        print("")
        print(
            f"❌ {location} エラー:"
        )
        print(e)

        try:
            body = page.locator(
                "body"
            ).inner_text()

            print(
                body[:3000]
            )

        except Exception:
            pass

        return [], str(e)

    finally:
        context.close()


# ============================================================
# メイン
# ============================================================

def main():

    found = []
    errors = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        # 府中・鮫洲・江東を順番に確認
        for location in LOCATIONS:

            dates, error = (
                check_location(
                    browser,
                    location
                )
            )

            for target_date in dates:

                found.append(
                    {
                        "location": location,
                        "date": target_date
                    }
                )

            if error:
                errors.append(
                    f"{location}: {error}"
                )

        browser.close()

    print("")
    print("======================")
    print("最終結果")
    print("======================")

    # 全会場で取得失敗した場合は、
    # 「空きなし」と誤判定させない
    if len(errors) == len(LOCATIONS):

        print(
            "❌ 全会場の監視に失敗しました"
        )

        for error in errors:
            print(error)

        raise Exception(
            "予約サイトの監視に失敗しました"
        )

    # 一部会場だけ失敗
    if errors:

        print(
            "⚠️ 一部会場でエラー:"
        )

        for error in errors:
            print(error)

    # 空きなし
    if not found:

        print(
            "✅ 正常に確認完了"
        )

        print(
            "現在、明日〜10/9に"
            "通知対象の空きはありません"
        )

        return

    # ========================================================
    # 重複削除
    # ========================================================

    unique = {}

    for item in found:

        key = (
            item["location"],
            item["date"]
        )

        unique[key] = item

    found = list(
        unique.values()
    )

    # 日付が早い順
    found.sort(
        key=lambda x: (
            x["date"],
            x["location"]
        )
    )

    print("")
    print(
        "🚨 通知対象の空き発見"
    )

    # ========================================================
    # Discord通知作成
    # ========================================================

    lines = [
        "🚨 本免学科試験の早い空きが出ています！",
        ""
    ]

    for item in found:

        d = item["date"]

        line = (
            f"✅ "
            f"{item['location']} "
            f"{d.month}/{d.day}"
        )

        print(line)

        lines.append(line)

    lines += [
        "",
        "明日〜10/9の枠です。",
        "すぐ予約サイトを確認してください。",
        START_URL
    ]

    send_discord(
        "\n".join(lines)
    )


if __name__ == "__main__":
    main()