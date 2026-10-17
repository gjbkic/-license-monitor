import os
import unittest
from unittest.mock import patch

from browser_config import DEFAULT_USER_AGENT, context_options


class BrowserConfigTests(unittest.TestCase):
    def test_unset_and_empty_variable_use_verified_default(self):
        for value in (None, '', '   '):
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                if value is not None:
                    os.environ['BOOKING_USER_AGENT'] = value
                self.assertEqual(context_options()['user_agent'], DEFAULT_USER_AGENT)

    def test_invalid_header_is_rejected(self):
        with patch.dict(os.environ, {'BOOKING_USER_AGENT': 'Browser\nOther: value'}):
            with self.assertRaises(ValueError):
                context_options()
