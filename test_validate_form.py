import os
import unittest
from unittest.mock import MagicMock, patch

from applicant import Applicant
from test_applicant import DATA
import validate_form


class FormValidationTests(unittest.TestCase):
    def test_without_run_confirmation_does_not_read_secret_or_launch_browser(self):
        with patch.dict(os.environ, {'FORM_FILL_TERMS_CONFIRMED': ''}), \
             patch.object(validate_form, 'load_applicant') as load, \
             patch.object(validate_form, 'sync_playwright') as launch:
            with self.assertRaises(validate_form.ValidationError):
                validate_form.run()
        load.assert_not_called()
        launch.assert_not_called()

    def test_personal_apis_and_writes_are_blocked(self):
        for method, url in [
            ('POST', 'https://license-test.tokyo-madoguchi-yoyaku.com/putres'),
            ('GET', 'https://license-test-tokyo-prd-police-pref-api.tokyo-madoguchi-yoyaku.com/customerops'),
            ('GET', 'https://example.com/telemetry.js'),
        ]:
            with self.subTest(method=method, url=url):
                route = MagicMock()
                route.request.method = method
                route.request.url = url
                validate_form.allow_static_request(route)
                route.abort.assert_called_once()
                route.continue_.assert_not_called()

    def test_matching_fields_are_verified_without_clicking(self):
        page = MagicMock()
        values = [DATA['name_kana'], DATA['certificate_number'], DATA['phone'], '2000', '01', '02']
        fields = []
        for value in values:
            field = MagicMock()
            field.count.return_value = 1
            field.is_visible.return_value = True
            field.input_value.return_value = value
            fields.append(field)
        page.locator.side_effect = fields
        validate_form.verify_fields(page, Applicant.from_dict(DATA))
        for field in fields:
            field.click.assert_not_called()

    def test_mismatch_does_not_disclose_personal_value(self):
        page = MagicMock()
        page.locator.return_value.count.return_value = 1
        page.locator.return_value.input_value.return_value = 'unexpected private value'
        with self.assertRaises(validate_form.ValidationError) as error:
            validate_form.verify_fields(page, Applicant.from_dict(DATA))
        self.assertNotIn('unexpected private value', str(error.exception))
