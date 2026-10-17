import os
import re
import json
from pathlib import Path
from urllib.parse import urlsplit
import requests

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright
from site_entry import discover_booking_entry, SiteEntryError
from browser_config import context_options


# ============================================================
# 設定
# ============================================================

START_URL = (
    "https://license-renew.tokyo-madoguchi-yoyaku.com/"
    "police-pref-tokyo/index_000.html"
)

# 明日から何日先まで監視するか。
# GitHub Actions側で MONITOR_DAYS_AHEAD を設定すれば変更できます。
MONITOR_DAYS_AHEAD = int(os.environ.get("MONITOR_DAYS_AHEAD", "45"))
if not 1 <= MONITOR_DAYS_AHEAD <= 90:
    raise ValueError("MONITOR_DAYS_AHEADは1〜90で指定してください")

def monitor_end_date(today):
    configured = os.environ.get("MONITOR_END_DATE", "").strip()
    if configured:
        return date.fromisoformat(configured)
    return today + timedelta(days=MONITOR_DAYS_AHEAD)


LOCATIONS = [
    "府中試験場",
    "鮫洲試験場",
    "江東試験場",
]


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

        response.raise_for_status()
        print("Discord送信完了")

    except Exception as e:
        print("Discord送信失敗:", type(e).__name__)
        raise RuntimeError("Discord通知を送信できませんでした") from None


# ============================================================
# アクセス制限
# ============================================================

class AccessBlocked(Exception):
    def __init__(self, status, reason):
        self.status = status
        self.reason = reason
        super().__init__(reason)


class ScanError(Exception):
    pass


class AccessGuard:
    def __init__(self):
        self.block = None
        self.load_errors = []

    def on_response(self, response):
        try:
            status = response.status
            resource_type = response.request.resource_type
            if status in (404, 500, 502, 503, 504) and resource_type in ("document", "script", "stylesheet"):
                self.load_errors.append((status, resource_type, urlsplit(response.url).path))

            if status == 429:
                self.block = (
                    429,
                    f"HTTP 429 Too Many Requests\n{response.url}"
                )

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
        if self.load_errors:
            status, resource_type, path = self.load_errors[0]
            raise ScanError(f"予約画面の読込失敗: HTTP {status} ({resource_type}) {path}")

        try:
            body = page.locator("body").inner_text(
                timeout=2000
            )

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
                int(data.get("consecutive_429", 0)),

            "cooldown_until":
                data.get("cooldown_until"),

            "was_blocked":
                bool(data.get("was_blocked", False)),
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
                f"🛑 429対策であと約"
                f"{minutes}分休止中"
            )

            return True

    except Exception:
        pass

    return False


def register_block(state, status, reason):

    state["was_blocked"] = True

    # --------------------------------------------------------
    # 403：休止しない
    # --------------------------------------------------------

    if status == 403:

        state["cooldown_until"] = None

        save_state(state)

        send_discord(
            "⚠️【本免監視】"
            "403またはアクセス制限らしき応答を検知しました。\n\n"
            f"{reason}\n\n"
            "今回の処理を停止しました。次回実行時に再確認します。"
        )

        return

    # --------------------------------------------------------
    # 429
    # --------------------------------------------------------

    count = (
        state.get("consecutive_429", 0)
        + 1
    )

    state["consecutive_429"] = count

    # 1回目：休止なし
    # 2回連続：10分
    # 3回以上：20分

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

        state["cooldown_until"] = (
            until.isoformat()
        )

    save_state(state)

    if cooldown_minutes == 0:
        action = (
            "今回の処理を停止しました。次回実行時に再確認します。"
        )

    else:
        action = (
            f"{cooldown_minutes}分だけ休止して"
            "次回実行時に再確認します。"
        )

    send_discord(
        "⚠️【本免監視】"
        "HTTP 429を検知しました。\n\n"
        f"{reason}\n\n"
        f"429連続: {count}回\n"
        f"{action}"
    )


def register_success(state):

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
            "今回の予約状況確認が完了しました。"
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

        **context_options(),

    )

    # 画像・フォント・動画だけカット。
    # JS/CSS/XHRは止めない。
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
# ページ操作
# ============================================================

def click_text(page, guard, text):
    print(f"クリック: {text}", flush=True)
    try:
        # SPAの読込完了前にcount()==0で失敗させない。
        locator = page.get_by_text(text, exact=True).filter(visible=True)
        locator.first.wait_for(state="visible", timeout=30000)
        guard.check(page)
        visible = [el for el in locator.all() if el.is_visible()]
        if len(visible) != 1:
            raise ScanError(f"「{text}」の表示候補が{len(visible)}件あります")
        visible[0].click(timeout=15000)
        guard.check(page)
    except AccessBlocked:
        raise
    except ScanError:
        raise
    except Exception:
        guard.check(page)
        raise ScanError(f"「{text}」の表示またはクリックに失敗しました") from None


def agree_terms(page):

    checkbox = page.locator(
        'input[name="TermsOfServiceCheck"]'
    ).first

    checkbox.wait_for(state="attached", timeout=30000)
    if not checkbox.is_checked():
        if checkbox.is_visible():
            checkbox.check()
        else:
            # The site's styled checkbox hides the native input. Click its
            # visible consent text rather than waiting for that input to show.
            page.get_by_text('「利用規約について」を読み、同意しました。', exact=True).click(timeout=15000)

    page.wait_for_timeout(300)

    if not checkbox.is_checked():

        raise ScanError(
            "利用規約に同意できません"
        )

    print(
        "利用規約チェック: OK"
    )


def get_year_month(page):

    body = page.locator(
        "body"
    ).inner_text()

    header = page.locator(".ui-datepicker-title").first
    if header.count() and header.is_visible():
        body = header.inner_text()
    match = re.search(
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月", body
    )

    if not match:
        return None, None

    return (
        int(match.group(1)),
        int(match.group(2))
    )


# ============================================================
# カレンダーまで
# ============================================================

def setup_calendar(
    page,
    guard,
    location
):

    print("")
    print(
        "================================"
    )
    print(
        f"開始: {location}"
    )
    print(
        "================================"
    )

    try:
        entry_url = discover_booking_entry()
    except SiteEntryError as error:
        raise ScanError(str(error)) from None
    response = page.goto(
        entry_url,
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
                f"HTTP {response.status}\n{entry_url}"
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

    # Venue selection starts an asynchronous calendar request.
    # Its DOM may exist while still hidden; wait for the rendered calendar.
    page.locator('.ui-datepicker-calendar').filter(visible=True).wait_for(
        state='visible', timeout=30000
    )
    guard.check(page)
    year, month = get_year_month(
        page
    )

    if year is None:

        raise ScanError(
            "カレンダー年月取得失敗"
        )

    print(
        f"{location}: "
        f"カレンダー到達 "
        f"{year}-{month:02d}"
    )

    return year, month


# ============================================================
# 月移動
# ============================================================

def click_previous_month(
    page,
    guard
):

    selectors = [
        ".ui-datepicker-prev",
        '[title*="前"]',
        '[aria-label*="前"]'
    ]

    for selector in selectors:

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
                    500
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

    selectors = [
        ".ui-datepicker-next",
        '[title*="次"]',
        '[aria-label*="次"]'
    ]

    for selector in selectors:

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
                    500
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
            raise ScanError(
                "表示月を取得できません"
            )

        if (
            year == target_year
            and month == target_month
        ):
            return

        current = (
            year * 12 + month
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
                raise ScanError(
                    f"{target_year}/"
                    f"{target_month}へ"
                    "移動できません"
                )

        else:

            if not click_previous_month(
                page,
                guard
            ):
                raise ScanError(
                    f"{target_year}/"
                    f"{target_month}へ"
                    "移動できません"
                )

    raise ScanError(
        "月移動回数が上限を超えました"
    )


# ============================================================
# 日付セル
# ============================================================

def get_day_cell_info(
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

            text = (
                cell.inner_text()
                .strip()
            )

            if text != str(day):
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

            # 前月/翌月のセルなら無視
            if (
                "ui-datepicker-other-month"
                in cls
            ):
                continue

            selectable = True

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
                selectable = False

            if (
                info["links"] == 0
                and info["buttons"] == 0
            ):
                selectable = False

            return cell, selectable

        except Exception:
            pass

    return None, False


# ============================================================
# 残席読み取り
# ============================================================

def parse_remaining_slots(body):
    """午前・午後の境界を越えて別枠の残席を対応付けない。"""
    headings = list(re.finditer(r"午前試験|午後試験", body))
    results = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        section = body[heading.end():end]
        remaining = re.search(r"残り\s*([0-9]+)\s*名", section)
        if not remaining:
            continue
        reception = re.search(r"受付時間\s*([0-9]{1,2}:[0-9]{2})", section)
        label = heading.group()
        if reception:
            label += " " + reception.group(1)
        results[label] = {"time": label, "remaining": int(remaining.group(1))}
    return list(results.values())


def read_remaining_slots(page):
    return parse_remaining_slots(page.locator("body").inner_text())


def wait_for_remaining_slots(page):

    # 最大約3秒待つ
    for _ in range(6):

        slots = (
            read_remaining_slots(
                page
            )
        )

        if slots:
            return slots

        page.wait_for_timeout(
            500
        )

    return []


# ============================================================
# 実際に日付クリック
# ============================================================

def inspect_date(
    page,
    guard,
    target_date
):

    move_to_month(
        page,
        guard,
        target_date.year,
        target_date.month
    )

    cell, selectable = (
        get_day_cell_info(
            page,
            target_date.day
        )
    )

    if cell is None:

        raise ScanError(
            f"{target_date}: "
            "カレンダーセルが見つかりません"
        )

    if not selectable:

        print(
            f"⚪ {target_date.month}/"
            f"{target_date.day} "
            "選択不可"
        )

        return []

    print(
        f"🔎 {target_date.month}/"
        f"{target_date.day} "
        "選択可能 → 実際にクリック"
    )

    try:

        link = cell.locator(
            "a"
        ).first

        if link.count() > 0:
            link.click()

        else:

            button = cell.locator(
                "button"
            ).first

            if button.count() == 0:
                raise ScanError(
                    "選択可能セル内に"
                    "リンク/ボタンがありません"
                )

            button.click()

        page.wait_for_timeout(
            500
        )

        guard.check(page)

    except AccessBlocked:
        raise

    except ScanError:
        raise

    except Exception as e:

        raise ScanError(
            f"{target_date}: "
            f"日付クリック失敗: {e}"
        )

    slots = wait_for_remaining_slots(
        page
    )

    # 選択可能だったのに残席表示を
    # 取得できなければ「0」と勝手に判断しない
    if not slots:

        raise ScanError(
            f"{target_date}: "
            "日付クリック後に"
            "残り○名を取得できません"
        )

    positive = []

    for slot in slots:

        print(
            f"   └ {slot['time']} "
            f"残り{slot['remaining']}名"
        )

        if slot["remaining"] > 0:

            positive.append(
                {
                    "date":
                        target_date,

                    "time":
                        slot["time"],

                    "remaining":
                        slot["remaining"]
                }
            )

    return positive


# ============================================================
# 1か月を完全確認
# ============================================================

def scan_month(
    page,
    guard,
    location,
    year,
    month,
    on_slot=None,
):

    print("")
    print(
        "--------------------------------"
    )
    print(
        f"📅 {location} "
        f"{year}/{month} 確認開始"
    )
    print(
        "--------------------------------"
    )

    move_to_month(
        page,
        guard,
        year,
        month
    )

    actual_year, actual_month = (
        get_year_month(page)
    )

    if (
        actual_year != year
        or actual_month != month
    ):

        raise ScanError(
            f"{location}: "
            f"{year}/{month}を"
            "表示できていません"
        )

    today = datetime.now(
        ZoneInfo("Asia/Tokyo")
    ).date()

    latest_alert_date = monitor_end_date(today)

    results = []

    checked_days = 0
    selectable_days = 0

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

        # 今日以前は不要
        if target_date <= today:
            continue

        # 今日から指定日数より先は不要
        if (
            target_date
            > latest_alert_date
        ):
            continue

        checked_days += 1

        cell, selectable = (
            get_day_cell_info(
                page,
                day
            )
        )

        if cell is None:

            raise ScanError(
                f"{location} "
                f"{target_date}: "
                "日付セルがありません"
            )

        if not selectable:

            print(
                f"⚪ {target_date.month}/"
                f"{target_date.day} "
                "選択不可"
            )

            continue

        selectable_days += 1

        slots = inspect_date(
            page,
            guard,
            target_date
        )

        for slot in slots:

            slot[
                "location"
            ] = location

            results.append(slot)
            if on_slot:
                on_slot(slot)

    print("")
    print(
        f"✅ {location} "
        f"{year}/{month} 確認完了"
    )

    print(
        f"   対象日数: "
        f"{checked_days}日"
    )

    print(
        f"   選択可能日: "
        f"{selectable_days}日"
    )

    print(
        f"   残席あり: "
        f"{len(results)}枠"
    )

    return results


# ============================================================
# 1会場を完全確認
# ============================================================

def scan_location(
    browser,
    location,
    on_slot=None,
):

    context = create_context(
        browser
    )

    page = context.new_page()

    guard = AccessGuard()

    page.on(
        "response",
        guard.on_response
    )

    try:

        shown_year, shown_month = setup_calendar(
            page,
            guard,
            location
        )

        today = datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).date()

        latest_alert_date = monitor_end_date(today)

        results = []

        # 過去月には戻らない。
        # 以前は9月・10月を固定で見ていたため、
        # 10月に入った後も9月へ戻ろうとして失敗していた。
        year = shown_year
        month = shown_month

        for _ in range(12):

            month_first = date(
                year,
                month,
                1
            )

            if month_first > latest_alert_date:
                break

            month_results = scan_month(
                page,
                guard,
                location,
                year,
                month,
                on_slot=on_slot,
            )

            results.extend(
                month_results
            )

            if (
                year == latest_alert_date.year
                and month == latest_alert_date.month
            ):
                break

            month += 1

            if month == 13:
                month = 1
                year += 1

        print("")
        print(
            "================================"
        )
        print(
            f"✅ {location}: "
            f"明日〜{latest_alert_date} 確認完了"
        )
        print(
            "================================"
        )

        return results

    except Exception as error:
        save_diagnostic(page, location, error)
        raise
    finally:
        context.close()


def save_diagnostic(page, location, error):
    """個人情報入力前の監視画面の障害情報だけを残す。"""
    folder = Path("diagnostics")
    folder.mkdir(exist_ok=True)
    record = {"location": location, "error_type": type(error).__name__}
    try:
        # クエリにセッション情報が含まれる可能性があるため除外。
        record["path"] = urlsplit(page.url).path
        record["title"] = page.title()
        record["calendar_visible"] = page.locator(".ui-datepicker-calendar").first.is_visible()
        record["visible_text_length"] = len(page.locator("body").inner_text(timeout=2000))
    except Exception:
        record["page_unavailable"] = True
    (folder / f"{location}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ============================================================
# 表示
# ============================================================

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

    if check_cooldown(
        state
    ):
        return

    try:

        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True
            )

            try:

                all_slots = []
                notified = set()

                def notify_slot(slot):
                    key = (slot["location"], slot["date"], slot["time"])
                    if key in notified:
                        return
                    send_discord(
                        "🚨【本免学科試験・残席あり】\n"
                        + slot_line(slot)
                        + "\n予約はまだ取得していません。\n" + START_URL
                    )
                    notified.add(key)

                completed_locations = 0
                failed_locations = []

                for location in LOCATIONS:

                    try:
                        slots = scan_location(browser, location, on_slot=notify_slot)
                    except ScanError as error:
                        failed_locations.append(f"{location}: {error}")
                        print(failed_locations[-1], flush=True)
                        continue
                    all_slots.extend(slots)
                    completed_locations += 1

                # 3会場全部終わっていなければ
                # 正常扱いしない
                if (
                    completed_locations
                    != len(LOCATIONS)
                ):

                    raise ScanError(
                        "全3会場の確認が完了していません: "
                        + "; ".join(failed_locations)
                    )

                # 全会場・全対象月確認成功
                register_success(
                    state
                )

                print("")
                print(
                    "################################"
                )
                print(
                    "✅ 3会場すべて "
                    "監視対象期間の確認完了"
                )
                print(
                    "################################"
                )

                if not all_slots:

                    print(
                        "通知対象の残席は"
                        "ありません"
                    )

                    return

                print(f"残席通知済み: {len(notified)}枠")

            finally:
                browser.close()

    except AccessBlocked as e:

        register_block(
            state,
            e.status,
            e.reason
        )

        raise SystemExit(2)

    except Exception as e:

        # 「空きなし」と誤認させず、
        # 監視失敗としてDiscordにも通知
        message = (
            "❌【本免監視エラー】"
            "予約状況を最後まで確認できませんでした。\n\n"
            f"{type(e).__name__}: {e}\n\n"
            "この回は「空きなし」とは判定していません。"
        )

        send_discord(
            message
        )

        raise


if __name__ == "__main__":
    main()
