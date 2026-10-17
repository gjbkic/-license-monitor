import unittest
from datetime import datetime, date
from unittest.mock import MagicMock, patch

import monitor


class MonitorTests(unittest.TestCase):
    def test_custom_terms_checkbox_uses_visible_consent_text(self):
        page = MagicMock()
        checkbox = page.locator.return_value.first
        checkbox.is_visible.return_value = False
        checkbox.is_checked.side_effect = [False, True]
        monitor.agree_terms(page)
        checkbox.wait_for.assert_called_once_with(state='attached', timeout=30000)
        checkbox.check.assert_not_called()
        page.get_by_text.assert_called_once_with('「利用規約について」を読み、同意しました。', exact=True)
        page.get_by_text.return_value.click.assert_called_once()

    def test_visible_native_terms_checkbox_is_supported(self):
        page = MagicMock()
        checkbox = page.locator.return_value.first
        checkbox.is_visible.return_value = True
        checkbox.is_checked.side_effect = [False, True]
        monitor.agree_terms(page)
        checkbox.check.assert_called_once()
        page.get_by_text.assert_not_called()

    def test_booking_context_uses_configured_user_agent(self):
        browser = MagicMock()
        with patch.dict(monitor.os.environ, {'BOOKING_USER_AGENT': 'TestBrowser/1.0'}):
            monitor.create_context(browser)
        self.assertEqual(browser.new_context.call_args.kwargs['user_agent'], 'TestBrowser/1.0')

    def test_missing_morning_count_does_not_use_afternoon_count(self):
        slots = monitor.parse_remaining_slots(
            '午前試験 受付時間 8:00 満席 午後試験 受付時間 11:00 残り 2 名'
        )
        self.assertEqual(slots, [{'time': '午後試験 11:00', 'remaining': 2}])

    def test_zero_and_positive_counts_are_separate(self):
        self.assertEqual(monitor.parse_remaining_slots(
            '午前試験 受付時間 8:00 残り0名 午後試験 受付時間 11:00 残り3名'
        ), [{'time': '午前試験 8:00', 'remaining': 0},
            {'time': '午後試験 11:00', 'remaining': 3}])

    def test_blank_screen_is_not_zero_availability(self):
        self.assertEqual(monitor.parse_remaining_slots(''), [])
        page = MagicMock()
        cell = MagicMock()
        cell.locator.return_value.first.count.return_value = 1
        with patch.object(monitor, 'read_remaining_slots', return_value=[]), \
             patch.object(monitor, 'move_to_month'), \
             patch.object(monitor, 'get_day_cell_info', return_value=(cell, True)):
            with self.assertRaisesRegex(monitor.ScanError, '残り○名を取得できません'):
                monitor.inspect_date(page, MagicMock(), date(2026, 10, 20))

    def test_fixed_end_includes_december_and_excludes_january(self):
        fixed_clock = MagicMock()
        fixed_clock.now.return_value = datetime(2026, 10, 17)
        with patch.dict(monitor.os.environ, {"MONITOR_END_DATE": "2026-12-31"}), \
             patch.object(monitor, 'datetime', fixed_clock), \
             patch.object(monitor, 'setup_calendar', return_value=(2026, 10)), \
             patch.object(monitor, 'scan_month', return_value=[]) as scan:
            monitor.scan_location(MagicMock(), '府中試験場')
        self.assertEqual([(c.args[3], c.args[4]) for c in scan.call_args_list],
                         [(2026, 10), (2026, 11), (2026, 12)])

    def test_expired_fixed_end_does_not_roll_into_next_year(self):
        with patch.dict(monitor.os.environ, {"MONITOR_END_DATE": "2026-12-31"}):
            self.assertLess(monitor.monitor_end_date(date(2027, 1, 1)), date(2027, 1, 1))

    def test_current_month_never_scans_fixed_september(self):
        browser = MagicMock()
        fixed_clock = MagicMock()
        fixed_clock.now.return_value = datetime(2026, 10, 17)
        with patch.object(monitor, 'datetime', fixed_clock), \
             patch.object(monitor, 'setup_calendar', return_value=(2026, 10)), \
             patch.object(monitor, 'scan_month', return_value=[]) as scan:
            monitor.scan_location(browser, '府中試験場')
        self.assertEqual([(c.args[3], c.args[4]) for c in scan.call_args_list],
                         [(2026, 10), (2026, 11), (2026, 12)])

    def test_slot_notified_before_later_day_failure(self):
        fixed_clock = MagicMock()
        fixed_clock.now.return_value = datetime(2026, 10, 17)
        found = {'date': date(2026, 10, 18), 'time': '午前試験 8:00', 'remaining': 1}
        callback = MagicMock()
        with patch.object(monitor, 'datetime', fixed_clock), \
             patch.object(monitor, 'move_to_month'), \
             patch.object(monitor, 'get_year_month', return_value=(2026, 10)), \
             patch.object(monitor, 'get_day_cell_info', return_value=(MagicMock(), True)), \
             patch.object(monitor, 'inspect_date', side_effect=[[found], monitor.ScanError('次の日が失敗')]):
            with self.assertRaises(monitor.ScanError):
                monitor.scan_month(MagicMock(), MagicMock(), '府中試験場', 2026, 10, callback)
        callback.assert_called_once_with(found)

    def test_other_locations_checked_after_one_location_fails(self):
        playwright = MagicMock()
        with patch.object(monitor, 'sync_playwright', return_value=playwright), \
             patch.object(monitor, 'load_state', return_value=monitor.default_state()), \
             patch.object(monitor, 'scan_location', side_effect=[monitor.ScanError('府中失敗'), [], []]) as scan, \
             patch.object(monitor, 'send_discord'), \
             patch.object(monitor, 'register_success') as success:
            with self.assertRaises(monitor.ScanError):
                monitor.main()
        self.assertEqual(scan.call_count, 3)
        success.assert_not_called()

    def test_discord_rejection_is_not_success(self):
        response = MagicMock()
        response.raise_for_status.side_effect = monitor.requests.HTTPError('webhook secret URL')
        with patch.object(monitor, 'DISCORD_WEBHOOK', 'https://example.invalid/test'), \
             patch.object(monitor.requests, 'post', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'Discord通知を送信できませんでした'):
                monitor.send_discord('test')

    def test_missing_required_script_is_load_failure_not_empty_calendar(self):
        guard = monitor.AccessGuard()
        response = MagicMock(status=404, url='https://license-test.tokyo-madoguchi-yoyaku.com/script.js?private=ignored')
        response.request.resource_type = 'script'
        guard.on_response(response)
        with self.assertRaisesRegex(monitor.ScanError, 'HTTP 404') as error:
            guard.check(MagicMock())
        self.assertNotIn('private', str(error.exception))
        self.assertIsNone(guard.block)

    def test_missing_image_does_not_block_calendar(self):
        guard = monitor.AccessGuard()
        response = MagicMock(status=404, url='https://example.com/logo.png')
        response.request.resource_type = 'image'
        guard.on_response(response)
        self.assertEqual(guard.load_errors, [])

    def test_access_block_is_failure_exit(self):
        with patch.object(monitor, 'sync_playwright', side_effect=monitor.AccessBlocked(429, 'rate limit')), \
             patch.object(monitor, 'load_state', return_value=monitor.default_state()), \
             patch.object(monitor, 'register_block'):
            with self.assertRaises(SystemExit) as error:
                monitor.main()
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
