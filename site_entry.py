"""Resolve the booking entry from the current official police information page."""
from functools import lru_cache
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import requests

OFFICIAL_URL = 'https://www.keishicho.metro.tokyo.lg.jp/menkyo/menkyo/web.html'
LINK_LABEL = '警視庁運転免許手続予約サイト'


class SiteEntryError(RuntimeError):
    pass


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.results = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = {'href': dict(attrs).get('href', ''), 'text': ''}

    def handle_data(self, data):
        if self.current is not None:
            self.current['text'] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.results.append(self.current)
            self.current = None


def resolve_booking_entry(html):
    parser = Links()
    parser.feed(html)
    matches = set()
    for link in parser.results:
        if LINK_LABEL not in link['text']:
            continue
        url = urljoin(OFFICIAL_URL, link['href'])
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.tokyo-madoguchi-yoyaku.com'):
            raise SiteEntryError('公式案内のリンク先ドメインが変わりました。送信前に接続先の確認が必要です。')
        if parsed.username or parsed.password:
            raise SiteEntryError('予約入口URLの形式を確認できません')
        matches.add(url)
    if len(matches) != 1:
        raise SiteEntryError('公式案内から予約入口を一意に取得できません。古いURLへは自動接続しません。')
    return matches.pop()


@lru_cache(maxsize=1)
def discover_booking_entry():
    try:
        response = requests.get(OFFICIAL_URL, timeout=20)
        response.raise_for_status()
        if urlsplit(response.url).hostname != 'www.keishicho.metro.tokyo.lg.jp':
            raise SiteEntryError('公式案内が別ドメインへ移動しました。接続先を確認してください。')
        response.encoding = 'utf-8'
        return resolve_booking_entry(response.text)
    except SiteEntryError:
        raise
    except Exception:
        raise SiteEntryError('警視庁の公式案内を取得できません。予約処理は開始していません。') from None
