"""User opens the applicant form; fill it without navigating or submitting."""
import argparse
import re
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from applicant import load_applicant, ProfileError
from site_entry import discover_booking_entry, SiteEntryError

ALLOWED_HOSTS = {
    'license-renew.tokyo-madoguchi-yoyaku.com',
    'license-test.tokyo-madoguchi-yoyaku.com',
}


class FormError(RuntimeError):
    pass


def field_info(control):
    # Only labels and option metadata are inspected, never existing entered values.
    return control.evaluate('''el => {
        const pieces = [el.getAttribute('aria-label'), el.getAttribute('placeholder'),
            ...Array.from(el.labels || []).map(x => x.innerText)];
        const row = el.closest('tr');
        if (row && row.innerText.length < 250) pieces.push(row.innerText);
        if (el.parentElement && el.parentElement.innerText.length < 160)
            pieces.push(el.parentElement.innerText);
        return {text: pieces.filter(Boolean).join(' '),
            tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
            name: el.getAttribute('name') || '', id: el.id || '',
            options: el.tagName === 'SELECT' ? Array.from(el.options).map(
                x => ({value:x.value, text:x.textContent.trim()})) : []};
    }''')


def build_plan(controls, applicant):
    patterns = {
        'name_kana': r'フリガナ|ふりがな|カナ|氏名|名前',
        'phone': r'電話|連絡先',
        'certificate_number': r'(卒業証明書|検査合格証明書|卒業証明書等|証明書).*(番号|Ｎｏ|No)',
    }
    plan = []
    for key, pattern in patterns.items():
        matches = [(control, info) for control, info in controls
                   if info['tag'] in ('input', 'textarea')
                   and info['type'] not in ('hidden', 'password', 'checkbox', 'radio')
                   and re.search(pattern, info['text'], re.I)]
        if len(matches) != 1:
            raise FormError(f'{key} の入力欄を一意に確認できません。まだ入力していません。')
        plan.append((matches[0][0], 'fill', getattr(applicant, key)))

    birth = [(c, i) for c, i in controls
             if i['type'] == 'date' or re.search(r'生年月日', i['text'])]
    single = [(c, i) for c, i in birth if i['tag'] == 'input' and i['type'] in ('date', 'text', '')]
    if len(single) == 1:
        if single[0][1]['type'] != 'date':
            raise FormError('生年月日のテキスト欄の形式を確認できません。まだ入力していません。')
        plan.append((single[0][0], 'fill', applicant.birth_date))
    else:
        year, month, day = map(int, applicant.birth_date.split('-'))
        for key, value, suffix in [('year', year, '年'), ('month', month, '月'), ('day', day, '日')]:
            matches = []
            for control, info in controls:
                if info['tag'] != 'select':
                    continue
                descriptor = ' '.join((info['text'], info['name'], info['id']))
                # A whole 生年月日 container alone cannot identify which of three selects is which.
                own = re.sub(r'生年月日', '', descriptor)
                if re.search(rf'{key}|{suffix}', own, re.I):
                    options = [o for o in info['options']
                               if o['value'].isdigit() and int(o['value']) == value
                               or re.fullmatch(rf'0*{value}\s*{suffix}?', o['text'])]
                    if len(options) == 1:
                        matches.append((control, options[0]['value']))
            if len(matches) != 1:
                raise FormError(f'生年月日の{suffix}欄を一意に確認できません。まだ入力していません。')
            plan.append((matches[0][0], 'select_option', matches[0][1]))
    # Never fill multiple applicant fields into the same DOM control.
    if len({id(control) for control, _, _ in plan}) != len(plan):
        raise FormError('入力欄の対応が重複しています。まだ入力していません。')
    return plan


def fill_applicant(page, applicant):
    url = urlsplit(page.url)
    if url.scheme != 'https' or url.hostname not in ALLOWED_HOSTS:
        raise FormError('警視庁の対象予約サイト以外には入力しません')
    controls = []
    for control in page.locator('input, select, textarea').all():
        if control.is_visible() and control.is_enabled():
            controls.append((control, field_info(control)))
    plan = build_plan(controls, applicant)
    try:
        for control, action, value in plan:
            getattr(control, action)(value)
    except Exception:
        # Playwright errors can contain the fill argument. Never propagate their text/traceback.
        raise FormError('入力中に画面が変わりました。入力済み項目を画面で確認してください。') from None
    return len(plan)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-profile', action='store_true')
    args = parser.parse_args()
    applicant = load_applicant()
    if args.check_profile:
        print('申込情報の設定・形式を確認しました。本人性や証明書の有効性の確認ではありません。')
        return
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            context = browser.new_context(locale='ja-JP')
            page = context.new_page()
            page.goto(discover_booking_entry(), wait_until='domcontentloaded')
            print('ブラウザで利用規約・手続区分を確認し、予約者情報の入力画面まで進んでください。')
            input('予約者情報の画面を開いたら、この端末でEnterを押してください: ')
            fill_applicant(page, applicant)
            print('申込情報を入力しました。ブラウザで内容を確認してください。予約はまだ確定していません。')
            input('会場・日時の選択や予約確定はブラウザで行ってください。終了するにはEnter: ')
        finally:
            browser.close()


if __name__ == '__main__':
    try:
        main()
    except (ProfileError, FormError, SiteEntryError) as error:
        print(str(error))
        raise SystemExit(1)
    except (EOFError, KeyboardInterrupt):
        print('処理を中断しました')
        raise SystemExit(1)
    except Exception:
        print('ブラウザ操作に失敗しました。申込情報を含む詳細ログは出力しません。')
        raise SystemExit(1)
