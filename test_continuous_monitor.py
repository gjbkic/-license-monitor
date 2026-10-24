import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import continuous_monitor as worker


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.starts = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def scan(self):
        self.starts.append(self.now)
        self.now += 37
        return True


class ContinuousMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.state = {'status': 'idle'}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(worker, 'CLOCK_PATH', Path(self.temp.name) / 'clock.json'))
        self.stack.enter_context(patch.object(worker.booking, 'read_booking_state', side_effect=lambda: self.state))
        self.stack.enter_context(patch.object(worker.monitor, 'monitor_end_date', return_value=__import__('datetime').date(2099, 1, 1)))
        self.stack.enter_context(patch.object(worker, 'write_handoff'))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def run_worker(self, **kwargs):
        options = dict(clock=self.clock.time, sleep=self.clock.sleep,
                       check=self.clock.scan, control=lambda: True, max_cycles=3)
        options.update(kwargs)
        return worker.run_loop(**options)

    def test_scan_starts_are_300_seconds_apart_including_scan_time(self):
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual(self.clock.starts, [1000, 1300, 1600])

    def test_job_handoff_preserves_next_start_instead_of_scanning_immediately(self):
        self.run_worker(max_cycles=2)
        self.clock.now += 60  # Setup in the following job.
        self.run_worker(max_cycles=1)
        self.assertEqual(self.clock.starts, [1000, 1300, 1600])

    def test_booked_and_notified_ends_loop_without_more_reservations(self):
        def booked():
            self.clock.scan()
            self.state.update(status='booked', notified=True)
            return True
        self.assertEqual(self.run_worker(check=booked), 0)
        self.assertEqual(self.clock.starts, [1000])
        worker.write_handoff.assert_called_once_with(False)

    def test_unknown_submission_stops_without_retry_or_handoff(self):
        def unknown():
            self.clock.scan()
            self.state['status'] = 'submitting'
            return False
        self.assertEqual(self.run_worker(check=unknown), 1)
        self.assertEqual(self.clock.starts, [1000])
        worker.write_handoff.assert_called_once_with(False)

    def test_remote_stop_prevents_new_scan(self):
        self.assertEqual(self.run_worker(control=lambda: False), 0)
        self.assertEqual(self.clock.starts, [])

    def test_control_fetch_failure_skips_scan_then_recovers_on_next_deadline(self):
        with patch.object(worker, 'control_enabled', side_effect=[None, True]) as control:
            self.run_worker(control=control, max_cycles=2)
        self.assertEqual(self.clock.starts, [1300])

    def test_slow_scan_skips_missed_deadlines_instead_of_bursting(self):
        def slow():
            self.clock.starts.append(self.clock.now)
            self.clock.now += 640
            return True
        self.run_worker(check=slow, max_cycles=2)
        self.assertEqual(self.clock.starts, [1000, 1900])

    def test_unnotified_booking_retries_notification_then_stops(self):
        self.state.update(status='booked', notified=False)
        def notify():
            self.clock.scan()
            self.state['notified'] = True
            return True
        self.assertEqual(self.run_worker(check=notify), 0)
        self.assertEqual(self.clock.starts, [1000])


if __name__ == '__main__':
    unittest.main()
