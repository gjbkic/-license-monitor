"""One operational preflight: notification availability and real booking path."""
import json
import os
import re
from datetime import date
from urllib.parse import urlsplit
import requests
from playwright.sync_api import sync_playwright
from applicant import load_applicant
from browser_config import context_options
from earliest import earliest_candidates
import auto_reserve as booking
import monitor


def prevent_reservation(route):
    if urlsplit(route.request.url).path.rstrip('/').split('/')[-1] in ('putres', 'cancel', 'getcrypto'):
        route.abort()
    else:
        route.continue_()


def main():
    applicant = load_applicant()
    webhook = os.environ.get('DISCORD_WEBHOOK', '')
    if not webhook:
        raise RuntimeError('DISCORD_WEBHOOKが未設定です。通知先を設定するまで運用は開始しません。')
    try:
        response = requests.get(webhook, timeout=15)
        response.raise_for_status()
        if not response.json().get('channel_id'):
            raise ValueError()
    except Exception:
        raise RuntimeError('Discord通知先を確認できませんでした。Webhookの値はログへ表示しません。') from None
    print('Discord通知先の有効性を確認しました。チャンネル情報はログへ表示しません。', flush=True)
    for key in ('AUTO_RESERVE_ENABLED', 'BOOKING_TERMS_ACCEPTED'):
        value = os.environ.get(key, '')
        print(key + ': ' + (value if value in ('true', 'false') else 'unset_or_other'), flush=True)
    # Read only the public, already referenced message script to check completion copy.
    url = 'https://license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/01/js/MKAYMA001MessagesJa.js'
    public = requests.get(url, headers={'User-Agent': context_options()['user_agent']}, timeout=20)
    public.raise_for_status()
    copy = re.sub(r'\\u([0-9a-fA-F]{4})', lambda match: chr(int(match[1], 16)), public.text)
    completion = [(key, value) for key, value in re.findall(r'(MKAYMA01M[0-9]+):"([^"]*)"', copy)
                  if '完了' in value and len(value) < 200]
    print('公開画面の完了文言: ' + json.dumps(completion, ensure_ascii=False), flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = monitor.create_context(browser)
            context.route('**/*', prevent_reservation)
            page = context.new_page()
            guard = monitor.AccessGuard()
            page.on('response', guard.on_response)
            try:
                booking.open_booking_calendar(page, guard, {'location': monitor.LOCATIONS[0]}, applicant)
                print('本人情報の確認を通過し、予約用カレンダーへ到達しました。', flush=True)
            except Exception as error:
                # Error messages from the site/browser may include personal values.
                print('予約経路の停止種別: ' + type(error).__name__, flush=True)
                errors = page.locator('input.error:visible, select.error:visible')
                print('入力エラー欄の数: ' + str(errors.count()), flush=True)
                raise RuntimeError('本人情報確認後の予約経路を検証できません。予約は作成していません。') from None
            finally:
                context.close()
            today = monitor.datetime.now(monitor.ZoneInfo('Asia/Tokyo')).date()
            candidates = earliest_candidates(browser, today, date(2026, 12, 31))
            print('最短候補（予約未取得）: ' + json.dumps(
                [{**row, 'date': row['date'].isoformat()} for row in candidates], ensure_ascii=False), flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print(str(error))
        raise SystemExit(1)
    except Exception as error:
        print('運用前検証が停止しました（' + type(error).__name__ + '）。個人情報を含む詳細は表示しません。')
        raise SystemExit(1)
