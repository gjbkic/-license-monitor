"""Fill the live applicant form, verify values, and stop before its Next button.

No screenshots, HTML, traces, request bodies, or applicant values are logged.
The terms checkbox requires confirmation for this run, not auto-reserve enablement.
"""
import os
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from applicant import load_applicant, ProfileError
from autofill import fill_applicant, FormError, ALLOWED_HOSTS
from browser_config import context_options
from site_entry import discover_booking_entry, SiteEntryError


class ValidationError(RuntimeError):
    pass


def allow_static_request(route):
    request = route.request
    parsed = urlsplit(request.url)
    # In particular, never call customerops, putres, cancel, or getcrypto APIs.
    if (request.method != 'GET' or parsed.scheme != 'https'
            or parsed.hostname not in ALLOWED_HOSTS
            or not parsed.path.endswith(('.html', '.js', '.css', '.json'))):
        route.abort()
        return
    route.continue_()


def verify_fields(page, applicant):
    expected = {
        '#strNameKana': applicant.name_kana,
        '#strGraduationCertificateNumber': applicant.certificate_number,
        '#strTelNumber': applicant.phone,
    }
    year, month, day = applicant.birth_date.split('-')
    expected.update({'#birthYear': year, '#birthMonth': month, '#birthDay': day})
    for selector, value in expected.items():
        field = page.locator(selector)
        if field.count() != 1 or not field.is_visible() or field.input_value() != value:
            raise ValidationError('入力欄の値を照合できませんでした。値はログに表示しません。')


def run():
    if os.environ.get('FORM_FILL_TERMS_CONFIRMED') != 'true':
        raise ValidationError('今回の検証で利用規約へ同意する確認が必要です。まだ入力していません。')
    applicant = load_applicant()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(**context_options())
            context.route('**/*', allow_static_request)
            page = context.new_page()
            response = page.goto(discover_booking_entry(), wait_until='domcontentloaded', timeout=60000)
            if response is None or response.status != 200:
                raise ValidationError('予約入口を正常に読み込めませんでした。')
            page.get_by_text('学科試験の予約はこちら', exact=True).click(timeout=30000)
            terms = page.locator('input[name="TermsOfServiceCheck"]')
            terms.check(timeout=30000)
            page.get_by_text('手続を開始する', exact=True).click(timeout=15000)
            page.get_by_text('学科試験', exact=True).filter(visible=True).click(timeout=30000)
            page.get_by_text('教習所卒業等', exact=True).filter(visible=True).click(timeout=30000)
            page.get_by_text('免許証のみ', exact=True).filter(visible=True).click(timeout=30000)
            page.get_by_text('予約者の情報を入力してください。', exact=True).wait_for(
                state='visible', timeout=30000)
            for selector, value in (('typeChoice', '1'), ('typeDetailChoice', '11'), ('newLicenseChoice', '1')):
                if not page.locator(f'input[name="{selector}"][value="{value}"]').is_checked():
                    raise ValidationError('教習所卒業等・免許証のみの手続区分を確認できません。')
            fill_applicant(page, applicant)
            verify_fields(page, applicant)
            # Do not click nextPageBtn03: it would call the customer check API.
            print('本人情報4項目の入力と6欄の照合に成功しました。進む・予約確定は押していません。')
        finally:
            browser.close()


if __name__ == '__main__':
    try:
        run()
    except (ValidationError, ProfileError, FormError, SiteEntryError) as error:
        print(str(error))
        raise SystemExit(1)
    except Exception:
        # Playwright errors can embed fill arguments and selected values.
        print('入力検証が停止しました。個人情報を含む詳細ログは出力しません。予約処理は実行していません。')
        raise SystemExit(1)
