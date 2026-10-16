import unittest
from site_entry import resolve_booking_entry, SiteEntryError


class EntryTests(unittest.TestCase):
    def test_official_link_is_read_from_current_html(self):
        url = 'https://license-renew.tokyo-madoguchi-yoyaku.com/new-entry.html'
        self.assertEqual(resolve_booking_entry(f'<a href="{url}"><span>警視庁運転免許手続予約サイト</span>（外部サイト）</a>'), url)

    def test_old_url_is_not_used_when_link_disappears(self):
        with self.assertRaises(SiteEntryError):
            resolve_booking_entry('<a href="https://example.com">別のページ</a>')

    def test_ambiguous_entries_are_not_guessed(self):
        with self.assertRaises(SiteEntryError):
            resolve_booking_entry('<a href="https://a.tokyo-madoguchi-yoyaku.com/one">警視庁運転免許手続予約サイト</a>'
                                  '<a href="https://a.tokyo-madoguchi-yoyaku.com/two">警視庁運転免許手続予約サイト</a>')

    def test_same_link_in_mobile_and_desktop_is_not_ambiguous(self):
        html = '<a href="https://license-renew.tokyo-madoguchi-yoyaku.com/a">警視庁運転免許手続予約サイト</a>'
        self.assertEqual(resolve_booking_entry(html * 2), 'https://license-renew.tokyo-madoguchi-yoyaku.com/a')

    def test_untrusted_redirect_target_is_rejected(self):
        for host in ['example.com', 'tokyo-madoguchi-yoyaku.com.evil.invalid']:
            with self.assertRaises(SiteEntryError):
                resolve_booking_entry(f'<a href="https://{host}/">警視庁運転免許手続予約サイト</a>')
