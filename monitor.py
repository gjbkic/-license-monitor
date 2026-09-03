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

# 現在確認できている最短枠が10/14なので、
# 10/13までを監視対象にする
CUTOFF = date(2026, 10, 14)

# 3会場すべて確認
LOCATIONS = [
    "府中試験場",
    "鮫洲試験場",
    "江東試験場",
]

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")


def click_text(page, text):
    print(f"クリック: {text}")

    locator = page.get_by_text(text, exact=True)

    if locator.count() == 0:
        raise Exception(f"「{text}」が見つかりません")

    # 見えている要素を優先
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


def send_discord(message):
    print("")
    print("========== 通知 ==========")
    print(message)
    print("==========================")

    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK がまだ設定されていません")
        return

    response = requests.post(
        DISCORD_WEBHOOK,
        json={"content": message},
        timeout=20,
    )

    print("Discord status:", response.status_code)

    if response.status_code not in (200, 204):
        print(response.text)


def get_year_month(page):
    body = page.locator("body").inner_text()

    m = re.search(
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月",
        body
    )

    if not m:
        return None, None

    return int(m.group(1)), int(m.group(2))


def agree_terms(page):
    print("利用規約に同意します")

    checkbox = page.locator(
        'input[name="TermsOfServiceCheck"]'
    ).first

    if checkbox.count() == 0:
        raise Exception(
            "利用規約のチェックボックスが見つかりません"
        )

    # このサイトは本体inputが画面上では非表示なので
    # JavaScript経由でクリックする
    checkbox.evaluate("el => el.click()")

    page.wait_for_timeout(700)

    if not checkbox.is_checked():
        raise Exception(
            "利用規約にチェックできませんでした"
        )

    print("利用規約チェック: OK")


def setup_to_location(page, location):
    print("")
    print("==============================")
    print(f"開始: {location}")
    print("==============================")

    page.goto(
        START_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(2500)

    print("入口タイトル:", page.title())

    # ① 学科試験予約
    click_text(
        page,
        "学科試験の予約はこちら"
    )

    # ② 規約同意
    agree_terms(page)

    # ③ 手続開始
    click_text(
        page,
        "手続を開始する"
    )

    # ④ 学科試験
    click_text(
        page,
        "学科試験"
    )

    # ⑤ 教習所卒業等
    click_text(
        page,
        "教習所卒業等"
    )

    # ⑥ 免許証のみ
    click_text(
        page,
        "免許証のみ"
    )

    # ⑦ 試験場
    click_text(
        page,
        location
    )

    page.wait_for_timeout(2000)

    body = page.locator("body").inner_text()

    if "日付を選択してください" not in body:
        print(body[:5000])
        raise Exception(
            f"{location}: カレンダーまで到達できませんでした"
        )

    year, month = get_year_month(page)

    if year is None:
        print(body[:5000])
        raise Exception(
            f"{location}: カレンダー年月を取得できません"
        )

    print(
        f"{location}: カレンダー到達 "
        f"{year}-{month:02d}"
    )


def click_previous_month(page):
    selectors = [
        ".ui-datepicker-prev",
        '[aria-label*="前"]',
        '[title*="前"]',
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

    # HTMLから前月ボタンらしきものを探す
    elements = page.locator(
        "a, button, div, span"
    )

    for i in range(elements.count()):
        try:
            el = elements.nth(i)

            if not el.is_visible():
                continue

            html = el.evaluate(
                "el => el.outerHTML"
            ).lower()

            if (
                "datepicker-prev" in html
                or 'class="prev' in html
            ):
                el.click()
                page.wait_for_timeout(1200)
                return True

        except Exception:
            pass

    return False


def click_next_month(page):
    selectors = [
        ".ui-datepicker-next",
        '[aria-label*="次"]',
        '[title*="次"]',
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

    elements = page.locator(
        "a, button, div, span"
    )

    for i in range(elements.count()):
        try:
            el = elements.nth(i)

            if not el.is_visible():
                continue

            html = el.evaluate(
                "el => el.outerHTML"
            ).lower()

            if (
                "datepicker-next" in html
                or 'class="next' in html
            ):
                el.click()
                page.wait_for_timeout(1200)
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


def find_clickable_day(page, day):
    """
    カレンダー上で実際に選択可能な日付を探す。
    薄いグレーの日は除外する。
    """

    candidates = page.get_by_text(
        str(day),
        exact=True
    )

    for i in range(candidates.count()):

        try:
            el = candidates.nth(i)

            if not el.is_visible():
                continue

            info = el.evaluate(
                """
                el => {
                    let node = el;

                    for (let i = 0; i < 5 && node; i++) {

                        const style =
                            window.getComputedStyle(node);

                        const cls =
                            String(node.className || "")
                            .toLowerCase();

                        if (
                            node.tagName === "A" ||
                            node.tagName === "BUTTON" ||
                            node.onclick ||
                            node.getAttribute("role") === "button" ||
                            style.cursor === "pointer"
                        ) {

                            return {
                                found: true,
                                disabled:
                                    node.disabled === true ||
                                    node.getAttribute(
                                        "aria-disabled"
                                    ) === "true" ||
                                    cls.includes("disabled") ||
                                    cls.includes("unselectable") ||
                                    style.pointerEvents === "none"
                            };
                        }

                        node = node.parentElement;
                    }

                    return {
                        found: false,
                        disabled: true
                    };
                }
                """
            )

            if (
                info["found"]
                and not info["disabled"]
            ):
                return el

        except Exception:
            pass

    return None


def read_remaining_slots(page):
    """
    選択した日の
    午前・午後の残席数を取得する。
    """

    body = page.locator("body").inner_text()

    results = []

    morning = re.search(
        r"午前試験"
        r".{0,200}?"
        r"8:00"
        r".{0,200}?"
        r"残り\s*(\d+)\s*名",
        body,
        re.S
    )

    if morning:
        results.append(
            (
                "午前試験 8:00",
                int(morning.group(1))
            )
        )

    afternoon = re.search(
        r"午後試験"
        r".{0,200}?"
        r"11:00"
        r".{0,200}?"
        r"残り\s*(\d+)\s*名",
        body,
        re.S
    )

    if afternoon:
        results.append(
            (
                "午後試験 11:00",
                int(afternoon.group(1))
            )
        )

    return results


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

    available = []

    for day in range(1, 32):

        try:
            d = date(
                year,
                month,
                day
            )
        except ValueError:
            continue

        # 過去日は不要
        if d < today:
            continue

        # 10/14以降は不要
        if d >= CUTOFF:
            continue

        element = find_clickable_day(
            page,
            day
        )

        if element is None:
            continue

        print(
            f"{location} "
            f"{d.isoformat()} "
            "→ 日付選択可能"
        )

        try:
            element.click()
            page.wait_for_timeout(700)

        except Exception as e:
            print(
                "日付クリック失敗:",
                d,
                e
            )
            continue

        slots = read_remaining_slots(
            page
        )

        if not slots:
            print(
                "  残席情報を取得できません"
            )
            continue

        for time_name, remaining in slots:

            print(
                f"  {time_name}: "
                f"残り{remaining}名"
            )

            if remaining > 0:

                available.append(
                    {
                        "location":
                            location,

                        "date":
                            d,

                        "time":
                            time_name,

                        "remaining":
                            remaining
                    }
                )

    return available


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
        setup_to_location(
            page,
            location
        )

        results = []

        # 9月全体
        results += scan_month(
            page,
            location,
            2026,
            9
        )

        # 10/1～10/13
        results += scan_month(
            page,
            location,
            2026,
            10
        )

        return results, None

    except Exception as e:

        print("")
        print(
            f"❌ {location} エラー:"
        )
        print(e)

        try:
            print("")
            print(
                page.locator(
                    "body"
                ).inner_text()[:5000]
            )
        except Exception:
            pass

        return [], str(e)

    finally:
        context.close()


def main():
    all_slots = []
    errors = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        for location in LOCATIONS:

            slots, error = check_location(
                browser,
                location
            )

            all_slots.extend(
                slots
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

    # 3会場全部失敗した場合、
    # 「空きなし」と誤表示せず
    # GitHub Actions自体をエラーにする
    if len(errors) == len(LOCATIONS):

        print(
            "❌ 全試験場の確認に失敗しました"
        )

        for error in errors:
            print(error)

        raise Exception(
            "予約サイトの監視に失敗しました"
        )

    if errors:
        print("")
        print("一部エラーあり:")

        for error in errors:
            print(error)

    if not all_slots:

        print("")
        print(
            "正常に確認できた試験場では、"
            "10/14より前の残席はありません"
        )

        return

    # 日付順に並べる
    all_slots.sort(
        key=lambda x: (
            x["date"],
            x["location"],
            x["time"]
        )
    )

    lines = [
        "🚨 本免学科試験の早い空きが出ています！",
        ""
    ]

    for slot in all_slots:

        d = slot["date"]

        lines.append(
            f"✅ {slot['location']} "
            f"{d.month}/{d.day} "
            f"{slot['time']} "
            f"残り{slot['remaining']}名"
        )

    lines += [
        "",
        "10/14より前の枠です。",
        "すぐ予約サイトを確認してください。",
        START_URL
    ]

    send_discord(
        "\n".join(lines)
    )


if __name__ == "__main__":
    main()