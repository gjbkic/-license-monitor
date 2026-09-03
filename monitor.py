import os
import re
import json
import time
import requests

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright


# ============================================================
# 設定
# ============================================================

START_URL = (
    "https://license-renew.tokyo-madoguchi-yoyaku.com/"
    "police-pref-tokyo/index_000.html"
)

# 10/9までを通知対象
LATEST_ALERT_DATE = date(2026, 10, 9)

LOCATIONS = [
    "府中試験場",
    "鮫洲試験場",
    "江東試験場",
]

RECHECK_SECONDS = 5

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

STATE_FILE = ".monitor_state.json"


# ============================================================
# Discord
# ============================================================

def send_discord(message):
    print("")
    print("========== Discord ==========")
    print(message)
    print("=============================")

    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK未設定")
        return

    try:
        response = requests.post(
            DISCORD_WEBHOOK,
            json={"content": message},
            timeout=15
        )

        print("Discord status:", response.status_code)

    except Exception as e:
        print("Discord送信失敗:", e)


# ============================================================
# アクセス制限検知
# ============================================================

class AccessBlocked(Exception):
    def __init__(self, status, reason):
        self.status = status
        self.reason = reason
        super().__init__(reason)


class AccessGuard:
    def __init__(self):
        self.block = None

    def on_response(self, response):
        try:
            status = response.status
            resource_type = response.request.resource_type

            # 429
            if status == 429:
                self.block = (
                    429,
                    f"HTTP 429 Too Many Requests\n{response.url}"
                )

            # メイン通信の403
            elif (
                status == 403
                and resource_type in (
                    "document",
                    "xhr",
                    "fetch"
                )
            ):
                self.block = (
                    403,
                    f"HTTP 403 Forbidden\n{response.url}"
                )

        except Exception:
            pass

    def check(self, page):
        if self.block:
            status, reason = self.block
            raise AccessBlocked(status, reason)

        # HTTPコード以外の制限画面も確認
        try:
            body = page.locator(
                "body"
            ).inner_text(timeout=2000)

            blocked_words = [
                "Too Many Requests",
                "Access Denied",
                "Forbidden",
                "アクセスが制限",
                "アクセスを制限",
                "アクセスが集中",
                "しばらく時間をおいて",
                "不正なアクセス",
                "CAPTCHA",
            ]

            for word in blocked_words:
                if word.lower() in body.lower():
                    raise AccessBlocked(
                        403,
                        f"アクセス制限画面を検知: {word}"
                    )

        except AccessBlocked:
            raise

        except Exception:
            pass


# ============================================================
# 状態保存
# ============================================================

def default_state():
    return {
        "consecutive_429": 0,
        "cooldown_until": None,
        "was_blocked": False,
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return default_state()

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        return {
            "consecutive_429":
                int(
                    data.get(
                        "consecutive_429",
                        0
                    )
                ),

            "cooldown_until":
                data.get(
                    "cooldown_until"
                ),

            "was_blocked":
                bool(
                    data.get(
                        "was_blocked",
                        False
                    )
                ),
        }

    except Exception:
        return default_state()


def save_state(state):
    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# クールダウン
# ============================================================

def check_cooldown(state):
    value = state.get("cooldown_until")

    if not value:
        return False

    try:
        until = datetime.fromisoformat(value)
        now = datetime.now(timezone.utc)

        if now < until:
            remaining = until - now

            minutes = max(
                1,
                int(
                    remaining.total_seconds()
                    / 60
                ) + 1
            )

            print(
                f"🛑 429対策で"
                f"あと約{minutes}分休止中"
            )

            return True

    except Exception:
        pass

    return False


def register_block(state, status, reason):
    """
    403:
        休止なし。
        次の5分後に普通に再試行。

    429:
        1回目 → 休止なし
        2回連続 → 10分
        3回以上 → 20分
    """

    previously_blocked = state.get(
        "was_blocked",
        False
    )

    state["was_blocked"] = True

    # --------------------------------------------------------
    # 403
    # --------------------------------------------------------

    if status == 403:
        # 403は429カウントには入れない
        state["cooldown_until"] = None

        save_state(state)

        # 弾かれ始めた時だけ通知
        if not previously_blocked:
            send_discord(
                "⚠️【本免監視】"
                "予約サイトから403または"
                "アクセス制限らしき応答を検知しました。\n\n"
                f"{reason}\n\n"
                "休止はせず、次の5分監視で"
                "そのまま再試行します。"
            )

        print("⚠️ 403検知。休止なし。")
        print(reason)

        return

    # --------------------------------------------------------
    # 429
    # --------------------------------------------------------

    count = (
        state.get(
            "consecutive_429",
            0
        )
        + 1
    )

    state["consecutive_429"] = count

    if count == 1:
        cooldown_minutes = 0

    elif count == 2:
        cooldown_minutes = 10

    else:
        cooldown_minutes = 20

    if cooldown_minutes == 0:
        state["cooldown_until"] = None

    else:
        until = (
            datetime.now(timezone.utc)
            + timedelta(
                minutes=cooldown_minutes
            )
        )

        state[
            "cooldown_until"
        ] = until.isoformat()

    save_state(state)

    if cooldown_minutes == 0:
        action_text = (
            "休止せず、次の5分監視で再試行します。"
        )
    else:
        action_text = (
            f"{cooldown_minutes}分だけ休止して"
            "自動的に再開します。"
        )

    # 最初の429、または休止段階が変わった時は通知
    if (
        count == 1
        or count == 2
        or count == 3
    ):
        send_discord(
            "⚠️【本免監視】"
            "HTTP 429（アクセス過多）を検知しました。\n\n"
            f"{reason}\n\n"
            f"429連続: {count}回\n"
            f"{action_text}"
        )

    print(
        f"⚠️ 429検知: {count}回連続"
    )
    print(action_text)


def register_success(state):
    """
    1周正常に確認できたら、
    429回数・休止状態を即リセット。
    """

    was_blocked = state.get(
        "was_blocked",
        False
    )

    old_429 = state.get(
        "consecutive_429",
        0
    )

    state["consecutive_429"] = 0
    state["cooldown_until"] = None
    state["was_blocked"] = False

    save_state(state)

    if was_blocked or old_429 > 0:
        send_discord(
            "✅【本免監視】"
            "予約サイトへのアクセスが復旧しました。\n"
            "通常の5分監視に戻ります。"
        )


# ============================================================
# ブラウザ
# ============================================================

def create_context(browser):
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

    # 監視に不要な重い通信をカット
    def route_handler(route):
        try:
            resource_type = (
                route.request.resource_type
            )

            if resource_type in (
                "image",
                "font",
                "media"
            ):
                route.abort()

            else:
                route.continue_()

        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    context.route(
        "**/*",
        route_handler
    )

    return context


# ============================================================
# 基本操作
# ============================================================

def click_text(
    page,
    guard,
    text
):
    print(f"クリック: {text}")

    locator = page.get_by_text(
        text,
        exact=True
    )

    if locator.count() == 0:
        raise Exception(
            f"「{text}」が見つかりません"
        )

    for i in range(
        locator.count()
    ):
        element = locator.nth(i)

        try:
            if element.is_visible():
                element.click()

                page.wait_for_timeout(
                    600
                )

                guard.check(page)

                return

        except AccessBlocked:
            raise

        except Exception:
            pass

    raise Exception(
        f"「{text}」をクリックできません"
    )


def agree_terms(page):
    checkbox = page.locator(
        'input[name="TermsOfServiceCheck"]'
    ).first

    if checkbox.count() == 0:
        raise Exception(
            "利用規約チェックが見つかりません"
        )

    checkbox.evaluate(
        "el => el.click()"
    )

    page.wait_for_timeout(300)

    if not checkbox.is_checked():
        raise Exception(
            "利用規約に同意できません"
        )

    print("利用規約チェック: OK")


def get_year_month(page):
    body = page.locator(
        "body"
    ).inner_text()

    match = re.search(
        r"(20\d{2})\s*年\s*"
        r"(\d{1,2})\s*月",
        body
    )

    if not match:
        return None, None

    return (
        int(match.group(1)),
        int(match.group(2))
    )


# ============================================================
# カレンダーまで移動
# ============================================================

def setup_calendar(
    page,
    guard,
    location
):
    print("")
    print("================================")
    print(f"開始: {location}")
    print("================================")

    response = page.goto(
        START_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(800)

    if response:
        if response.status in (
            403,
            429
        ):
            raise AccessBlocked(
                response.status,
                f"HTTP {response.status}\n{START_URL}"
            )

    guard.check(page)

    click_text(
        page,
        guard,
        "学科試験の予約はこちら"
    )

    agree_terms(page)

    click_text(
        page,
        guard,
        "手続を開始する"
    )

    click_text(
        page,
        guard,
        "空き状況カレンダー"
    )

    click_text(
        page,
        guard,
        "教習所卒業等"
    )

    click_text(
        page,
        guard,
        "免許証のみ"
    )

    click_text(
        page,
        guard,
        location
    )

    year, month = get_year_month(page)

    if year is None:
        raise Exception(
            "カレンダー年月取得失敗"
        )

    print(
        f"{location}: "
        f"{year}-{month:02d}"
    )


# ============================================================
# 月移動
# ============================================================

def click_previous_month(
    page,
    guard
):
    for selector in [
        ".ui-datepicker-prev",
        '[title*="前"]',
        '[aria-label*="前"]'
    ]:

        try:
            el = page.locator(
                selector
            ).first

            if (
                el.count() > 0
                and el.is_visible()
            ):
                el.click()

                page.wait_for_timeout(
                    400
                )

                guard.check(page)

                return True

        except AccessBlocked:
            raise

        except Exception:
            pass

    return False


def click_next_month(
    page,
    guard
):
    for selector in [
        ".ui-datepicker-next",
        '[title*="次"]',
        '[aria-label*="次"]'
    ]:

        try:
            el = page.locator(
                selector
            ).first

            if (
                el.count() > 0
                and el.is_visible()
            ):
                el.click()

                page.wait_for_timeout(
                    400
                )

                guard.check(page)

                return True

        except AccessBlocked:
            raise

        except Exception:
            pass

    return False


def move_to_month(
    page,
    guard,
    target_year,
    target_month
):
    for _ in range(6):

        year, month = get_year_month(
            page
        )

        if year is None:
            return False

        if (
            year == target_year
            and month == target_month
        ):
            return True

        current = (
            year * 12
            + month
        )

        target = (
            target_year * 12
            + target_month
        )

        if current < target:
            if not click_next_month(
                page,
                guard
            ):
                return False

        else:
            if not click_previous_month(
                page,
                guard
            ):
                return False

    return False


# ============================================================
# 日付セル
# ============================================================

def find_day_cell(
    page,
    day
):
    cells = page.locator(
        ".ui-datepicker-calendar td"
    )

    for i in range(
        cells.count()
    ):
        try:
            cell = cells.nth(i)

            if (
                cell.inner_text().strip()
                != str(day)
            ):
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
                        el.querySelectorAll(
                            "a"
                        ).length,

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

            if (
                "ui-datepicker-unselectable"
                in cls
                or
                "ui-state-disabled"
                in cls
                or
                info[
                    "ariaDisabled"
                ] == "true"
            ):
                return None

            if (
                info["links"] == 0
                and
                info["buttons"] == 0
            ):
                return None

            return cell

        except Exception:
            pass

    return None


# ============================================================
# 残席
# ============================================================

def read_remaining_slots(page):
    body = page.locator(
        "body"
    ).inner_text()

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
        )
    ]

    for (
        label,
        section,
        time_text
    ) in definitions:

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
                    "remaining":
                        int(
                            match.group(1)
                        )
                }
            )

    return results


def check_date(
    page,
    guard,
    target_date
):
    if not move_to_month(
        page,
        guard,
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

    try:
        link = cell.locator(
            "a"
        ).first

        if link.count() > 0:
            link.click()

        else:
            cell.locator(
                "button"
            ).first.click()

        page.wait_for_timeout(500)

        guard.check(page)

    except AccessBlocked:
        raise

    except Exception:
        return []

    slots = read_remaining_slots(page)

    positive = []

    for slot in slots:

        print(
            f"{target_date} "
            f"{slot['time']} "
            f"残り{slot['remaining']}名"
        )

        if slot["remaining"] > 0:
            positive.append(
                {
                    "date": target_date,
                    "time": slot["time"],
                    "remaining":
                        slot["remaining"]
                }
            )

    return positive


# ============================================================
# 会場スキャン
# ============================================================

def scan_location(
    browser,
    location
):
    context = create_context(browser)
    page = context.new_page()

    guard = AccessGuard()

    page.on(
        "response",
        guard.on_response
    )

    try:
        setup_calendar(
            page,
            guard,
            location
        )

        today = datetime.now(
            ZoneInfo(
                "Asia/Tokyo"
            )
        ).date()

        results = []

        for year, month in [
            (2026, 9),
            (2026, 10)
        ]:

            if not move_to_month(
                page,
                guard,
                year,
                month
            ):
                continue

            for day in range(
                1,
                32
            ):

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

                # 10/9まで
                if (
                    target_date
                    > LATEST_ALERT_DATE
                ):
                    continue

                cell = find_day_cell(
                    page,
                    day
                )

                if cell is None:
                    continue

                slots = check_date(
                    page,
                    guard,
                    target_date
                )

                for slot in slots:
                    slot[
                        "location"
                    ] = location

                    results.append(
                        slot
                    )

        return results

    finally:
        context.close()


# ============================================================
# 5秒後再確認
# ============================================================

def recheck_slot(
    browser,
    slot
):
    context = create_context(browser)
    page = context.new_page()

    guard = AccessGuard()

    page.on(
        "response",
        guard.on_response
    )

    try:
        setup_calendar(
            page,
            guard,
            slot["location"]
        )

        results = check_date(
            page,
            guard,
            slot["date"]
        )

        for result in results:
            if (
                result["time"]
                == slot["time"]
            ):
                return result

        return None

    finally:
        context.close()


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
    state = load_state()

    # 429連続時のみ、必要なら休止
    if check_cooldown(state):
        return

    try:
        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True
            )

            try:
                all_slots = []

                for location in LOCATIONS:
                    slots = scan_location(
                        browser,
                        location
                    )

                    all_slots.extend(slots)

                # 3会場確認成功
                register_success(state)

                if not all_slots:
                    print(
                        "✅ 正常に監視完了。"
                        "通知対象の空きなし"
                    )
                    return

                # 空きは即通知
                for slot in all_slots:

                    send_discord(
                        "🚨【速報】"
                        "本免学科試験の空きを検知！\n\n"
                        + slot_line(slot)
                        + "\n\n"
                        + f"{RECHECK_SECONDS}秒後に"
                        "再確認します。\n"
                        + START_URL
                    )

                    time.sleep(
                        RECHECK_SECONDS
                    )

                    confirmed = recheck_slot(
                        browser,
                        slot
                    )

                    if confirmed:

                        confirmed[
                            "location"
                        ] = slot[
                            "location"
                        ]

                        send_discord(
                            "✅【再確認OK】"
                            f"{RECHECK_SECONDS}秒後も"
                            "空いています！\n\n"
                            + slot_line(
                                confirmed
                            )
                            + "\n\n"
                            + "すぐ予約してください。\n"
                            + START_URL
                        )

                    else:

                        send_discord(
                            "⚠️【再確認】"
                            f"{RECHECK_SECONDS}秒後には"
                            "空きが消えていました。\n\n"
                            + slot_line(slot)
                            + "\n\n"
                            + "一瞬のキャンセル枠だった"
                            "可能性があります。"
                        )

            finally:
                browser.close()

    except AccessBlocked as e:

        register_block(
            state,
            e.status,
            e.reason
        )

        return


if __name__ == "__main__":
    main()