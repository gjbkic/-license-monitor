"""Scan for a slot and reserve once with the user's saved applicant data.

Live end-to-end operation is unverified while the official application returns 404.
Unknown screens and unknown submission outcomes stop rather than click again.
"""
import json
import base64
import hashlib
from cryptography.fernet import Fernet
import os
import re
from datetime import date
from pathlib import Path
from playwright.sync_api import sync_playwright
from applicant import load_applicant, ProfileError
from autofill import fill_applicant, FormError
import monitor
from site_entry import discover_booking_entry, SiteEntryError

STATE_PATH = Path('.booking_state.json')


class BookingError(RuntimeError):
    pass


class SubmissionUnknown(BookingError):
    pass


class Finished(BaseException):
    pass


def read_booking_state():
    if not STATE_PATH.exists():
        return {'status': 'idle'}
    try:
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        if state.get('status') not in ('idle', 'submitting', 'booked', 'unavailable'):
            raise ValueError()
        return state
    except Exception:
        raise BookingError('予約状態ファイルが読めません。重複予約を防ぐため停止します。') from None


def write_booking_state(status, slot=None, receipt=None):
    state = {'status': status}
    if slot:
        state.update(location=slot['location'], date=slot['date'].isoformat(), time=slot['time'])
    if receipt:
        state['receipt_encrypted'] = receipt_cipher().encrypt(receipt.encode('ascii')).decode('ascii')
    temp = STATE_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    if os.name != 'nt':
        temp.chmod(0o600)
    temp.replace(STATE_PATH)



def receipt_cipher():
    webhook = os.environ.get('DISCORD_WEBHOOK', '')
    if not webhook:
        raise BookingError('予約結果を保護するDISCORD_WEBHOOKがありません')
    key = hashlib.sha256(('license-monitor-receipt-v1:' + webhook).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def notify_booked(state):
    try:
        receipt = receipt_cipher().decrypt(state['receipt_encrypted'].encode()).decode('ascii')
        response = monitor.requests.post(os.environ['DISCORD_WEBHOOK'], json={'content':
            '✅【本免・予約取得成功】\n'
            + state['location'] + ' ' + state['date'] + ' ' + state['time']
            + '\n受付番号: ' + receipt
            + '\n予約完了画面と受付番号の発行を確認しました。'}, timeout=15)
        response.raise_for_status()
    except Exception:
        raise BookingError('予約取得済みですが成功通知を送信できません。再実行では予約せず通知だけ再試行します。') from None
    state['notified'] = True
    temp = STATE_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    if os.name != 'nt':
        temp.chmod(0o600)
    temp.replace(STATE_PATH)
    print('予約取得成功。受付番号をDiscordに送信しました（ログには表示しません）。')


def compact(text):
    return re.sub(r'[\s\u3000-]', '', text)


def validate_confirmation(body, slot, applicant):
    d = slot['date']
    date_pattern = rf'{d.year}\s*(?:年|[/-])\s*0?{d.month}\s*(?:月|[/-])\s*0?{d.day}(?:日|\b)'
    expected_time = slot['time'].split()
    if slot['location'] not in body or not re.search(date_pattern, body):
        raise BookingError('予約確認画面の会場または日付が希望枠と一致しません')
    if any(value not in body for value in expected_time):
        raise BookingError('予約確認画面の試験枠または受付時間が一致しません')
    if compact(applicant.name_kana) not in compact(body):
        raise BookingError('予約確認画面の氏名を照合できません')
    if compact(applicant.certificate_number) not in compact(body):
        raise BookingError('予約確認画面の卒業証明書等の番号を照合できません')


def parse_receipt(body):
    found = re.search(r'受付番号\s*[:：]?\s*([0-9]{4}[\s\u3000-]*[0-9]{4}[\s\u3000-]*[0-9]{4})(?![0-9])', body)
    return re.sub(r'[^0-9]', '', found.group(1)) if found else None


def visible_text(page, text):
    return page.get_by_text(text, exact=True).filter(visible=True)


def next_button(page):
    # nextPageBtnNN is the public frontend's documented common.js button convention.
    buttons = page.locator('[id^="nextPageBtn"]').filter(visible=True)
    if buttons.count() != 1:
        raise BookingError('次の画面へ進むボタンを一意に確認できません')
    return buttons


def open_booking_calendar(page, guard, slot, applicant):
    response = page.goto(discover_booking_entry(), wait_until='domcontentloaded', timeout=60000)
    guard.check(page)
    if response and response.status >= 400:
        raise BookingError(f'入口ページの読込失敗 HTTP {response.status}')
    monitor.click_text(page, guard, '学科試験の予約はこちら')
    guard.check(page)
    # The operator explicitly enables this mode only after reading the official terms.
    monitor.agree_terms(page)
    monitor.click_text(page, guard, '手続を開始する')
    filled = False
    selected_location = False
    for _ in range(18):
        guard.check(page)
        if page.locator('.ui-datepicker-calendar').filter(visible=True).count():
            if not filled or not selected_location:
                raise BookingError('本人情報入力と会場選択を確認できないままカレンダーが表示されました')
            return
        if visible_text(page, '予約者の情報を入力してください。').count():
            if filled:
                raise BookingError('本人情報画面に戻りました。入力内容や既存予約を確認してください')
            fill_applicant(page, applicant)
            filled = True
            next_button(page).click()
            page.wait_for_timeout(800)
            continue
        if visible_text(page, slot['location']).count():
            monitor.click_text(page, guard, slot['location'])
            selected_location = True
            continue
        advanced = False
        for choice in ('学科試験', '教習所卒業等', '免許証のみ', '卒業証明書をお持ちの方'):
            if visible_text(page, choice).count() == 1:
                monitor.click_text(page, guard, choice)
                advanced = True
                break
        if not advanced:
            # Do not guess which branch/checkbox/button applies on an unknown page.
            raise BookingError('予約手続の画面構成を確認できません。予約確定は行っていません。')
    raise BookingError('予約画面の遷移回数が上限を超えました')


def select_trial_slot(page, guard, slot):
    positives = monitor.inspect_date(page, guard, slot['date'])
    if not any(item['time'] == slot['time'] for item in positives):
        return False
    parts = slot['time'].split()
    pattern = r'^\s*' + re.escape(parts[0])
    if len(parts) > 1:
        pattern += r'\s*[（(]?\s*受付時間\s*' + re.escape(parts[1]) + r'\s*[)）]?\s*$'
    else:
        pattern += r'\s*$'
    choice = page.get_by_text(re.compile(pattern)).filter(visible=True)
    if choice.count() != 1:
        raise BookingError('選択対象の試験時刻を一意に確認できません')
    choice.click()
    guard.check(page)
    return True


def submit_once(page, guard, slot, applicant):
    confirm = visible_text(page, '予約を確定する')
    confirm.wait_for(state='visible', timeout=15000)
    if confirm.count() != 1:
        raise BookingError('予約確定ボタンを一意に確認できません')
    validate_confirmation(page.locator('body').inner_text(), slot, applicant)
    # Persist BEFORE clicking. An unknown result prevents any new submission.
    write_booking_state('submitting', slot)
    try:
        confirm.click()
        for _ in range(30):
            guard.check(page)
            body = page.locator('body').inner_text()
            if visible_text(page, '予約完了').count():
                receipt = parse_receipt(body)
                if not receipt:
                    raise SubmissionUnknown('予約完了画面に受付番号が見つかりません。再予約せず確認してください。')
                write_booking_state('booked', slot, receipt)
                return receipt
            if 'お申込みの日時は予約が満席となりました' in body:
                write_booking_state('unavailable', slot)
                return None
            page.wait_for_timeout(500)
    except SubmissionUnknown:
        raise
    except Exception:
        raise SubmissionUnknown('予約送信後の結果を確定できません。再送せず予約状況を確認してください。') from None
    raise SubmissionUnknown('予約送信後の結果待ちが時間切れです。再送せず予約状況を確認してください。')


def attempt_reservation(browser, slot, applicant):
    context = monitor.create_context(browser)
    page = context.new_page()
    guard = monitor.AccessGuard()
    page.on('response', guard.on_response)
    try:
        open_booking_calendar(page, guard, slot, applicant)
        if not select_trial_slot(page, guard, slot):
            return None
        return submit_once(page, guard, slot, applicant)
    except (SubmissionUnknown, monitor.AccessBlocked):
        raise
    except (BookingError, FormError):
        raise
    except Exception:
        # Browser errors can contain personal fill arguments; omit original text.
        raise BookingError('予約画面の操作が失敗しました。個人情報を含む詳細はログへ出しません。') from None
    finally:
        # Never write screenshots, HTML, traces or personal page data to public Actions artifacts.
        context.close()


def run_once():
    if os.environ.get('AUTO_RESERVE_ENABLED') != 'true':
        raise BookingError('AUTO_RESERVE_ENABLED=true の設定が必要です')
    if os.environ.get('BOOKING_TERMS_ACCEPTED') != 'true':
        raise BookingError('公式利用規約を確認し、BOOKING_TERMS_ACCEPTED=true を設定してください')
    if not os.environ.get('DISCORD_WEBHOOK'):
        raise BookingError('予約結果を受け取るDISCORD_WEBHOOKを設定してください')
    applicant = load_applicant()
    state = read_booking_state()
    if state['status'] == 'submitting':
        raise SubmissionUnknown('前回の予約送信結果が未確認です。再送せず予約状況を確認してください。')
    if state['status'] == 'booked':
        if not state.get('notified'):
            notify_booked(state)
        print('予約取得済みです。新しい予約は行いません。')
        return
    cooldown = monitor.load_state()
    if monitor.check_cooldown(cooldown):
        return
    today = monitor.datetime.now(monitor.ZoneInfo('Asia/Tokyo')).date()
    end = monitor.monitor_end_date(today)
    if today >= end:
        print('予約対象期間は終了しました。')
        return
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            def found(slot):
                if not today < slot['date'] <= end or slot['location'] not in monitor.LOCATIONS:
                    raise BookingError('対象期間・会場の範囲外です')
                monitor.send_discord('🚨【本免・残席あり】\n' + monitor.slot_line(slot) + '\nこれから予約を試みます。')
                receipt = attempt_reservation(browser, slot, applicant)
                if receipt:
                    notify_booked(read_booking_state())
                    raise Finished()
            for location in monitor.LOCATIONS:
                monitor.scan_location(browser, location, on_slot=found)
            monitor.register_success(cooldown)
            print('今回の確認では予約取得に至りませんでした。')
        except Finished:
            return
        except monitor.AccessBlocked as error:
            monitor.register_block(cooldown, error.status, error.reason)
            raise BookingError('アクセス制限で今回の処理を停止しました') from None
        finally:
            browser.close()


if __name__ == '__main__':
    try:
        run_once()
    except (BookingError, ProfileError, FormError, monitor.ScanError, SiteEntryError) as error:
        print(str(error))
        raise SystemExit(1)
    except Exception:
        print('自動予約処理が停止しました。個人情報を含む詳細ログは出力しません。')
        raise SystemExit(1)
