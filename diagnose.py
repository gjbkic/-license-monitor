"""Read-only diagnosis before accepting terms or supplying applicant information."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from playwright.sync_api import sync_playwright
from site_entry import OFFICIAL_URL, discover_booking_entry
from browser_config import context_options


OUT = Path('public-diagnostics')


class PublicScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.scripts.append(attrs['src'])


def public_form_source(context, page_url, index_script):
    # Fetch the exact static page named by the public index script. This only reads
    # source files: no terms checkbox, application JS, API or reservation is executed.
    match = re.search(r'location\.href\s*=\s*[\'"](\./01/html/main\.html\?lang=)[\'"]', index_script)
    if not match:
        return {'status': 'not_linked'}
    url = urljoin(page_url, match.group(1) + 'ja')
    response = context.request.get(url, timeout=15000)
    result = {'url': safe_url(url), 'status': response.status}
    if not response.ok:
        return result
    content = response.body()
    if len(content) > 2000000:
        return {**result, 'error': 'source_too_large'}
    (OUT / 'form-public.html').write_bytes(content)
    parser = PublicScripts()
    parser.feed(content.decode('utf-8'))
    for index, src in enumerate(parser.scripts):
        script_url = urljoin(url, src)
        if urlsplit(script_url).netloc != urlsplit(page_url).netloc:
            continue
        # Common scripts have already been captured on the index page.
        if not re.search(r'MKAYMA[^/]+\.js$', urlsplit(script_url).path):
            continue
        script = context.request.get(script_url, timeout=15000)
        body = script.body()
        result.setdefault('scripts', []).append({'url': safe_url(script_url), 'status': script.status})
        if script.ok and len(body) <= 2000000:
            (OUT / f'form-script-{index}.js').write_bytes(body)
    return result


def safe_url(url):
    parsed = urlsplit(url)
    return f'{parsed.scheme}://{parsed.netloc}{parsed.path}'


def main():
    OUT.mkdir(exist_ok=True)
    network = []
    console = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(**context_options())
        page = context.new_page()
        def response_seen(response):
            if response.request.resource_type in ('document', 'script', 'stylesheet', 'xhr', 'fetch'):
                network.append({'url': safe_url(response.url), 'status': response.status,
                                'type': response.request.resource_type,
                                'headers': {key: value for key, value in response.headers.items()
                                            if key in ('server', 'age', 'x-cache', 'x-amz-cf-pop',
                                                       'content-length', 'vary')}})
        page.on('response', response_seen)
        page.on('requestfailed', lambda request: network.append(
            {'url': safe_url(request.url), 'failure': request.failure}))
        page.on('pageerror', lambda error: console.append(str(error)[:300]))
        try:
            page.goto(discover_booking_entry(), wait_until='domcontentloaded', timeout=60000)
            page.get_by_text('学科試験の予約はこちら', exact=True).click(timeout=30000)
            try:
                page.get_by_text('手続を開始する', exact=True).wait_for(state='visible', timeout=30000)
            except Exception:
                pass
            details = {'official_source': OFFICIAL_URL, 'entry': discover_booking_entry(), 'url': safe_url(page.url), 'title': page.title(),
                       'body': page.locator('body').inner_text()[:20000],
                       'user_agent': context_options()['user_agent']}
            details['entry_ready'] = '手続を開始する' in details['body']
            scripts = page.locator('script[src]').evaluate_all('(els)=>els.map(el=>el.src)')
            details['scripts'] = [safe_url(url) for url in scripts]
            # Only public JavaScript resources referenced by the actual page, same origin.
            for index, url in enumerate(scripts):
                if urlsplit(url).netloc != urlsplit(page.url).netloc:
                    continue
                response = context.request.get(url, timeout=15000)
                content = response.body()
                details.setdefault('script_fetches', []).append(
                    {'url': safe_url(url), 'status': response.status, 'bytes': len(content)})
                if response.ok and len(content) <= 2000000:
                    (OUT / f'script-{index}.js').write_bytes(content)
                    if urlsplit(url).path.endswith('/MKAYMA001Index.js'):
                        details['public_form_source'] = public_form_source(
                            context, page.url, content.decode('utf-8'))
            (OUT / 'page.json').write_text(json.dumps(details, ensure_ascii=False, indent=2))
            print('PAGE', json.dumps(details, ensure_ascii=False), flush=True)
            if not details['entry_ready'] or any(
                    item['status'] != 200 for item in details.get('script_fetches', [])):
                raise SystemExit('予約入口または必要なJavaScriptの読込検証に失敗しました')
        finally:
            (OUT / 'network.json').write_text(json.dumps(network, ensure_ascii=False, indent=2))
            (OUT / 'page-errors.json').write_text(json.dumps(console, ensure_ascii=False, indent=2))
            print('NETWORK', json.dumps(network, ensure_ascii=False), flush=True)
            print('PAGE_ERRORS', json.dumps(console, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == '__main__':
    main()
