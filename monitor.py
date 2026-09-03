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

# 10/14より前に空きが出たら通知
CUTOFF = date(2026, 10, 14)

# 3会場全部チェック
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

    for i in range(locator.count()):
        el = locator.nth(i)

        try:
            if el.is_visible():
                el.click()
                page.wait_for_timeout(1000)
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

    # 実際のinputが非表示なのでJavaScriptから操作
    checkbox.evaluate("el => el.click()")

    page.wait_for_timeout(500)

    if not checkbox.is_checked():
        raise Exception(
            "利用規約にチェックできませんでした"
        )

    print("利用規約チェック: OK")


def send_discord(message):
    print("")
    print("========== 通知 ==========")
    print(message)
    print("==========================")

    if not DISCORD_WEBHOOK:
        print(
            "DISCORD_WEBHOOK未設定なので"
            "Discordにはまだ送りません"
        )
        return

    r = requests.post(
        DISCORD_WEBHOOK,
        json={"content": message},
        timeout=20
    )

    print("Discord status:", r.status_code)


def get_year_month(page):
    body = page.locator("body").inner_text()

    m = re.search(
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月",
        body
    )

    if not m:
        return None, None

    return int(m.group(1)), int(m.group(2))


def setup_calendar(page, location):
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

    page.wait_for_timeout(2000)

    print("入口タイトル:", page.title())

    # 最初の画面
    click_text(
        page,
        "学科試験の予約はこちら"
    )

    # 規約
    agree_terms(page)

    click_text(
        page,
        "手続を開始する"
    )

    # ★ここが重要
    # 実際の予約ではなく空き状況カレンダーへ
    click_text(
        page,
        "空き状況カレンダー"
    )

    # 受験項目
    click_text(
        page,
        "教習所卒業等"
    )

    # 免許保有形態
    click_text(
        page,
        "免許証のみ"
    )

    # 会場
    click_text(
        page,
        location
    )

    page.wait_for_timeout(1500)

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

            if (
                el.count() > 0
                and el.is_visible()
            ):
                el.click()
                page.wait_for_timeout(800)
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

            if (
                el.count() > 0
                and el.is_visible()
            ):
                el.click()
                page.wait_for_timeout(800)
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
        target = (
            target_year * 12
            + target_month
        )

        if current < target:

            if not click_next_month(page):
                return False

        else:

            if not click_previous_month(page):
                return False

    return False


def get_available_days(
    page,
    year,
    month
):
    """
    jQuery UIカレンダー上で
    実際に選択可能な日だけ取得する。

    グレーで選べない日は除外。
    """

    available = []

    today = datetime.now(
        ZoneInfo("Asia/Tokyo")
    ).date()

    cells = page.locator(
        ".ui-datepicker-calendar td"
    )

    print(
        f"カレンダーセル数: "
        f"{cells.count()}"
    )

    for i in range(cells.count()):

        try:
            cell = cells.nth(i)

            text = cell.inner_text().strip()

            if not text.isdigit():
                continue

            day = int(text)

            try:
                d = date(
                    year,
                    month,
                    day
                )
            except ValueError:
                continue

            # 過去日は無視
            if d < today:
                continue

            # 10/14以降は対象外
            if d >= CUTOFF:
                continue

            info = cell.evaluate(
                """
                el => ({
                    cls: String(
                        el.className || ""
                    ),
                    ariaDisabled:
                        el.getAttribute(
                            "aria-disabled"
                        ),
                    links:
                        el.querySelectorAll("a").length,
                    buttons:
                        el.querySelectorAll(
                            "button"
                        ).length
                })
                """
            )

            cls = (
                info["cls"]
                or ""
            ).lower()

            # 明示的に無効なら除外
            if (
                "ui-datepicker-unselectable"
                in cls
                or
                "ui-state-disabled"
                in cls
                or
                info["ariaDisabled"]
                == "true"
            ):
                continue

            # jQuery datepickerでは
            # 選択できる日は基本的に<a>になる
            clickable = (
                info["links"] > 0
                or info["buttons"] > 0
            )

            if not clickable:
                continue

            available.append(d)

            print(
                "✅ 選択可能:",
                d.isoformat()
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
        f"===== "
        f"{location} "
        f"{year}/{month} "
        f"====="
    )

    if not move_to_month(
        page,
        year,
        month
    ):
        raise Exception(
            f"{location}: "
            f"{year}/{month}へ"
            "移動できません"
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

        # 9月
        dates += scan_month(
            page,
            location,
            2026,
            9
        )

        # 10/1～13
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
            print(
                page.locator(
                    "body"
                ).inner_text()[:3000]
            )
        except Exception:
            pass

        return [], str(e)

    finally:
        context.close()


def main():

    found = []
    errors = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        for location in LOCATIONS:

            dates, error = (
                check_location(
                    browser,
                    location
                )
            )

            for d in dates:
                found.append(
                    {
                        "location":
                            location,
                        "date":
                            d
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

    # 全部失敗した場合
    if len(errors) == len(LOCATIONS):

        print(
            "❌ 全会場の監視に失敗"
        )

        for e in errors:
            print(e)

        raise Exception(
            "予約サイトの監視に失敗しました"
        )

    # 一部だけ失敗
    if errors:

        print("一部会場でエラー:")

        for e in errors:
            print(e)

    # 空きなし
    if not found:

        print(
            "✅ 正常に確認完了"
        )

        print(
            "現在、10/14より前の"
            "空きはありません"
        )

        return

    # 重複除去
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

    found.sort(
        key=lambda x: (
            x["date"],
            x["location"]
        )
    )

    print("")
    print("🚨 空き発見")

    lines = [
        "🚨 本免学科試験の"
        "早い空きが出ています！",
        ""
    ]

    for item in found:

        d = item["date"]

        line = (
            f"✅ {item['location']} "
            f"{d.month}/{d.day}"
        )

        print(line)

        lines.append(line)

    lines += [
        "",
        "10/14より前の枠です。",
        "すぐ予約サイトを"
        "確認してください。",
        START_URL
    ]

    send_discord(
        "\n".join(lines)
    )


if __name__ == "__main__":
    main()