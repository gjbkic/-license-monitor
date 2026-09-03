import os
import re
import requests
from datetime import date
from playwright.sync_api import sync_playwright

URL = "https://license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/calendar/01/html/main.html?lang=ja"

# 現在確認できている最短空き日
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


def get_current_year_month(page):
    body = page.locator("body").inner_text()

    # 「2026年 10月」「2026 年 10 月」などに対応
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", body)

    if not m:
        return None, None

    return int(m.group(1)), int(m.group(2))


def scan_month(page):
    year, month = get_current_year_month(page)

    if not year:
        print("年月を取得できませんでした")
        print(page.locator("body").inner_text()[:3000])
        return []

    print(f"調査中: {year}-{month:02d}")

    found = []

    # カレンダー内の日付候補を探す
    for day in range(1, 32):
        elements = page.get_by_text(str(day), exact=True)

        for i in range(elements.count()):
            try:
                el = elements.nth(i)

                if not el.is_visible():
                    continue

                info = el.evaluate("""
                el => {
                    const parent = el.parentElement;

                    const getInfo = (node) => {
                        if (!node) return {};
                        const style = window.getComputedStyle(node);

                        return {
                            tag: node.tagName,
                            cls: String(node.className || ""),
                            disabled: node.disabled === true,
                            ariaDisabled: node.getAttribute("aria-disabled"),
                            pointerEvents: style.pointerEvents,
                            opacity: style.opacity,
                            color: style.color,
                            cursor: style.cursor
                        };
                    };

                    return {
                        self: getInfo(el),
                        parent: getInfo(parent),
                        grandparent: getInfo(parent ? parent.parentElement : null)
                    };
                }
                """)

                text_info = str(info).lower()

                # 明示的にdisabledなら除外
                if (
                    info["self"].get("disabled")
                    or info["parent"].get("disabled")
                    or info["grandparent"].get("disabled")
                    or info["self"].get("ariaDisabled") == "true"
                    or info["parent"].get("ariaDisabled") == "true"
                    or info["grandparent"].get("ariaDisabled") == "true"
                    or "disabled" in text_info
                ):
                    continue

                # pointer-events:none はクリック不可
                if (
                    info["self"].get("pointerEvents") == "none"
                    or info["parent"].get("pointerEvents") == "none"
                ):
                    continue

                # 日付として有効か確認
                try:
                    d = date(year, month, day)
                except ValueError:
                    continue

                # 実際にクリック可能か判定
                try:
                    clickable = el.evaluate("""
                    el => {
                        let node = el;

                        for (let i = 0; i < 3 && node; i++, node = node.parentElement) {
                            if (
                                node.tagName === "BUTTON" ||
                                node.tagName === "A" ||
                                node.onclick ||
                                node.getAttribute("role") === "button"
                            ) {
                                const style = window.getComputedStyle(node);

                                if (
                                    node.disabled === true ||
                                    node.getAttribute("aria-disabled") === "true" ||
                                    style.pointerEvents === "none"
                                ) {
                                    return false;
                                }

                                return true;
                            }
                        }

                        return false;
                    }
                    """)
                except Exception:
                    clickable = False

                if clickable:
                    found.append(d)
                    print("予約可能候補:", d.isoformat())

            except Exception as e:
                continue

    return sorted(set(found))


def click_previous_month(page):
    selectors = [
        ".ui-datepicker-prev",
        'a[title*="前"]',
        'button[title*="前"]',
        'a[aria-label*="前"]',
        'button[aria-label*="前"]',
    ]

    for selector in selectors:
        try:
            el = page.locator(selector).first

            if el.count() > 0 and el.is_visible():
                el.click()
                page.wait_for_timeout(1500)
                return True
        except Exception:
            pass

    # 左矢印っぽい画像やボタンを探す
    try:
        candidates = page.locator("a, button")

        for i in range(candidates.count()):
            el = candidates.nth(i)

            if not el.is_visible():
                continue

            html = el.evaluate("el => el.outerHTML")

            if any(word in html.lower() for word in [
                "prev",
                "previous",
                "back",
                "left"
            ]):
                el.click()
                page.wait_for_timeout(1500)
                return True

    except Exception:
        pass

    print("前月ボタンを押せませんでした")
    return False


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={
                "width": 1280,
                "height": 1200
            },
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            )
        )

        print("予約サイトを開きます")

        page.goto(
            URL,
            wait_until="networkidle",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        print("タイトル:", page.title())

        all_available = []

        # 最初に表示されている月を確認
        year, month = get_current_year_month(page)

        if not year:
            print("最初のカレンダー年月を取得できません")
            print(page.locator("body").inner_text()[:5000])
            browser.close()
            return

        print(f"最初の表示月: {year}-{month:02d}")

        # 10月が表示されている想定
        if year == 2026 and month == 10:
            all_available += scan_month(page)

            # 9月へ移動
            if click_previous_month(page):
                year2, month2 = get_current_year_month(page)

                if year2 == 2026 and month2 == 9:
                    all_available += scan_month(page)
                else:
                    print(
                        "前月へ移動しましたが、"
                        f"2026-09ではありません: {year2}-{month2}"
                    )

        # もし9月が最初に表示されている場合
        elif year == 2026 and month == 9:
            all_available += scan_month(page)

        else:
            print(
                "想定外の月が表示されています:",
                f"{year}-{month:02d}"
            )

        browser.close()

    all_available = sorted(set(all_available))

    print("")
    print("予約可能と判定した日:")

    if not all_available:
        print("なし")

    for d in all_available:
        print(d.isoformat())

    # 10/14より前だけ通知対象
    earlier = [
        d
        for d in all_available
        if d < CUTOFF
    ]

    if earlier:
        dates = "\n".join(
            f"✅ {d.month}/{d.day}"
            for d in earlier
        )

        notify(
            "🚨 本免試験、10/14より前に空きが出た可能性あり！\n\n"
            + dates
            + "\n\n"
            + "すぐ予約サイトを確認してください。\n"
            + URL
        )

    else:
        print("10/14より前の空きはありません")


if __name__ == "__main__":
    main()