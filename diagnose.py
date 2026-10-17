"""Read-only diagnosis before accepting terms or supplying applicant information."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from site_entry import OFFICIAL_URL, discover_booking_entry
from browser_config import context_options


OUT = Path('public-diagnostics')


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
            (OUT / 'page.json').write_text(json.dumps(details, ensure_ascii=False, indent=2))
            print('PAGE', json.dumps(details, ensure_ascii=False), flush=True)
        finally:
            (OUT / 'network.json').write_text(json.dumps(network, ensure_ascii=False, indent=2))
            (OUT / 'page-errors.json').write_text(json.dumps(console, ensure_ascii=False, indent=2))
            print('NETWORK', json.dumps(network, ensure_ascii=False), flush=True)
            print('PAGE_ERRORS', json.dumps(console, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == '__main__':
    main()
