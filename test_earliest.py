import unittest
from datetime import date
from unittest.mock import MagicMock, patch
import earliest
import monitor


class EarliestTests(unittest.TestCase):
    def test_earlier_other_venue_wins_and_morning_wins_same_day(self):
        rows = [
            [{'location': '府中試験場', 'date': date(2026, 11, 2), 'time': '午前試験 8:00', 'remaining': 1}],
            [{'location': '鮫洲試験場', 'date': date(2026, 10, 20), 'time': '午後試験 11:00', 'remaining': 1}],
            [{'location': '江東試験場', 'date': date(2026, 10, 20), 'time': '午前試験 8:30', 'remaining': 1}],
        ]
        with patch.object(earliest, 'first_at_location', side_effect=rows):
            result = earliest.earliest_candidates(MagicMock(), date(2026, 10, 17), date(2026, 12, 31))
        self.assertEqual([x['location'] for x in result], ['江東試験場', '鮫洲試験場', '府中試験場'])

    def test_first_available_day_stops_before_later_days(self):
        context = MagicMock()
        found = {'date': date(2026, 10, 20), 'time': '午前試験 8:00', 'remaining': 1}
        with patch.object(monitor, 'create_context', return_value=context), \
             patch.object(monitor, 'setup_calendar'), \
             patch.object(monitor, 'inspect_date', side_effect=[[], [], [found]]) as inspect:
            result = earliest.first_at_location(MagicMock(), '府中試験場', date(2026, 10, 17), date(2026, 12, 31))
        self.assertEqual(result[0]['date'], date(2026, 10, 20))
        self.assertEqual(inspect.call_count, 3)
        context.close.assert_called_once()

    def test_incomplete_venue_scan_cannot_claim_global_earliest(self):
        with patch.object(earliest, 'first_at_location', side_effect=monitor.ScanError('failed')):
            with self.assertRaises(monitor.ScanError):
                earliest.earliest_candidates(MagicMock(), date(2026, 10, 17), date(2026, 12, 31))
