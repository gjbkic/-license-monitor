import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from applicant import Applicant
import auto_reserve as booking
from test_applicant import DATA

SLOT = {'location': '府中試験場', 'date': date(2026, 11, 2),
        'time': '午前試験 8:00', 'remaining': 1}
BODY = '予約内容を確認してください。 府中試験場 2026年11月2日 午前試験 8:00 テスト タロウ 000123456789'


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path_patch = patch.object(booking, 'STATE_PATH', Path(self.temp.name) / 'state.json')
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.env = patch.dict(os.environ, {'DISCORD_WEBHOOK': 'https://example.invalid/test',
                                          'AUTO_RESERVE_ENABLED': 'true', 'BOOKING_TERMS_ACCEPTED': 'true'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.applicant = Applicant.from_dict(DATA)

    def test_disabled_mode_never_launches_browser(self):
        with patch.dict(os.environ, {'AUTO_RESERVE_ENABLED': 'false'}), \
             patch.object(booking, 'sync_playwright') as launch:
            with self.assertRaises(booking.BookingError):
                booking.run_once()
        launch.assert_not_called()

    def test_unresolved_submission_never_launches_browser(self):
        booking.write_booking_state('submitting', SLOT)
        with patch.object(booking, 'load_applicant', return_value=self.applicant), \
             patch.object(booking, 'sync_playwright') as launch:
            with self.assertRaises(booking.SubmissionUnknown):
                booking.run_once()
        launch.assert_not_called()

    def test_wrong_date_never_clicks_confirm(self):
        page = MagicMock()
        page.locator.return_value.inner_text.return_value = BODY.replace('11月2日', '11月3日')
        confirm = MagicMock()
        confirm.count.return_value = 1
        with patch.object(booking, 'visible_text', return_value=confirm):
            with self.assertRaises(booking.BookingError):
                booking.submit_once(page, MagicMock(), SLOT, self.applicant)
        confirm.click.assert_not_called()
        self.assertEqual(booking.read_booking_state()['status'], 'idle')

    def test_unknown_result_is_not_retried(self):
        page = MagicMock()
        page.locator.return_value.inner_text.return_value = BODY
        confirm = MagicMock()
        confirm.count.return_value = 1
        confirm.click.side_effect = RuntimeError('network disconnected')
        with patch.object(booking, 'visible_text', return_value=confirm):
            with self.assertRaises(booking.SubmissionUnknown):
                booking.submit_once(page, MagicMock(), SLOT, self.applicant)
        confirm.click.assert_called_once()
        self.assertEqual(booking.read_booking_state()['status'], 'submitting')

    def test_success_requires_completion_and_receipt(self):
        page = MagicMock()
        page.locator.return_value.inner_text.side_effect = [BODY, '予約完了 受付番号 1234 5678 9012']
        confirm = MagicMock()
        confirm.count.return_value = 1
        done = MagicMock()
        done.count.return_value = 1
        with patch.object(booking, 'visible_text', side_effect=[confirm, done]):
            receipt = booking.submit_once(page, MagicMock(), SLOT, self.applicant)
        self.assertEqual(receipt, '123456789012')
        state = booking.read_booking_state()
        self.assertEqual(state['status'], 'booked')
        self.assertNotIn(receipt, booking.STATE_PATH.read_text())
        for value in DATA.values():
            self.assertNotIn(value, booking.STATE_PATH.read_text())
        self.assertEqual(booking.receipt_cipher().decrypt(state['receipt_encrypted'].encode()).decode(), receipt)

    def test_completion_without_receipt_stops_as_unknown(self):
        page = MagicMock()
        page.locator.return_value.inner_text.side_effect = [BODY, '予約完了']
        confirm = MagicMock()
        confirm.count.return_value = 1
        with patch.object(booking, 'visible_text', return_value=confirm):
            with self.assertRaises(booking.SubmissionUnknown):
                booking.submit_once(page, MagicMock(), SLOT, self.applicant)
        self.assertEqual(booking.read_booking_state()['status'], 'submitting')

    def test_sold_out_is_not_marked_booked(self):
        page = MagicMock()
        page.locator.return_value.inner_text.side_effect = [BODY, 'お申込みの日時は予約が満席となりました']
        confirm = MagicMock()
        confirm.count.return_value = 1
        not_done = MagicMock()
        not_done.count.return_value = 0
        with patch.object(booking, 'visible_text', side_effect=[confirm, not_done]):
            self.assertIsNone(booking.submit_once(page, MagicMock(), SLOT, self.applicant))
        self.assertEqual(booking.read_booking_state()['status'], 'unavailable')

    def test_booked_state_retries_notification_only(self):
        booking.write_booking_state('booked', SLOT, '123456789012')
        with patch.object(booking, 'load_applicant', return_value=self.applicant), \
             patch.object(booking, 'sync_playwright') as launch, \
             patch.object(booking.monitor.requests, 'post', return_value=MagicMock()) as post:
            booking.run_once()
        launch.assert_not_called()
        self.assertTrue(booking.read_booking_state()['notified'])
        self.assertIn('123456789012', post.call_args.kwargs['json']['content'])


if __name__ == '__main__':
    unittest.main()
