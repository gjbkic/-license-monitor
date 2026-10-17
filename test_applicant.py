import json
import unittest
from unittest.mock import MagicMock, patch

from applicant import Applicant, ProfileError, load_applicant
from autofill import FormError, build_plan, fill_applicant


# Fictional values are only used in unit tests, never submitted to a service.
DATA = {'name_kana': 'テスト タロウ', 'birth_date': '2000-01-02',
        'phone': '0312345678', 'certificate_number': '000123456789'}


def control(label, kind='input', input_type='text', name='', options=None):
    return MagicMock(), {'text': label, 'tag': kind, 'type': input_type,
                         'name': name, 'id': '', 'options': options or []}


class ApplicantTests(unittest.TestCase):
    def test_leading_zeroes_preserved_and_repr_redacted(self):
        profile = Applicant.from_dict(DATA)
        self.assertEqual(profile.certificate_number, '000123456789')
        for value in DATA.values():
            self.assertNotIn(value, repr(profile))

    def test_number_fields_must_be_strings(self):
        with self.assertRaisesRegex(ProfileError, 'certificate_number'):
            Applicant.from_dict({**DATA, 'certificate_number': 123})

    def test_certificate_format_matches_public_form_length_rule(self):
        value = '第０００１２３号'
        self.assertEqual(Applicant.from_dict({**DATA, 'certificate_number': value}).certificate_number, value)
        with self.assertRaises(ProfileError):
            Applicant.from_dict({**DATA, 'certificate_number': '0' * 101})

    def test_public_form_birth_selects_with_zero_padded_values(self):
        # IDs and select structure verified in the public main.html source.
        fields = [control('氏名（カナ又はアルファベット）', name='strNameKana'),
                  control('連絡先電話番号', input_type='tel', name='strTelNumber'),
                  control('卒業証明書等の番号', name='strGraduationCertificateNumber'),
                  control('生年月日', kind='select', name='birthYear', options=[{'value': '2000', 'text': '2000'}]),
                  control('', kind='select', name='birthMonth', options=[{'value': '00', 'text': '---'}, {'value': '01', 'text': '1'}]),
                  control('', kind='select', name='birthDay', options=[{'value': '00', 'text': '---'}, {'value': '02', 'text': '2'}])]
        plan = build_plan(fields, Applicant.from_dict(DATA))
        self.assertEqual([(action, value) for _, action, value in plan[-3:]],
                         [('select_option', '2000'), ('select_option', '01'), ('select_option', '02')])

    def test_invalid_birth_has_no_personal_value_in_error(self):
        invalid = '2000-02-31'
        with self.assertRaises(ProfileError) as error:
            Applicant.from_dict({**DATA, 'birth_date': invalid})
        self.assertNotIn(invalid, str(error.exception))

    def test_environment_profile_is_used_without_file_access(self):
        with patch.dict('os.environ', {'APPLICANT_JSON': json.dumps(DATA)}), \
             patch('applicant.Path.read_text') as read:
            self.assertEqual(load_applicant().phone, DATA['phone'])
        read.assert_not_called()

    def test_unrelated_site_is_rejected_before_any_dom_access(self):
        page = MagicMock(url='https://example.com')
        with self.assertRaises(FormError):
            fill_applicant(page, Applicant.from_dict(DATA))
        page.locator.assert_not_called()

    def test_missing_field_does_not_partially_fill_other_fields(self):
        name = control('氏名 カタカナ')
        phone = control('電話番号')
        with self.assertRaises(FormError):
            build_plan([name, phone], Applicant.from_dict(DATA))
        name[0].fill.assert_not_called()
        phone[0].fill.assert_not_called()

    def test_ambiguous_name_does_not_fill(self):
        fields = [control('氏名'), control('氏名'), control('電話番号'),
                  control('卒業証明書等の番号'), control('生年月日', input_type='date')]
        with self.assertRaises(FormError):
            build_plan(fields, Applicant.from_dict(DATA))
        for item, _ in fields:
            item.fill.assert_not_called()

    def test_fill_uses_profile_but_never_clicks_submit(self):
        fields = [control('氏名 カタカナ'), control('電話番号'),
                  control('卒業証明書等の番号'), control('生年月日', input_type='date')]
        page = MagicMock(url='https://license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/index.html')
        page.locator.return_value.all.return_value = [c for c, _ in fields]
        for item, info in fields:
            item.evaluate.return_value = info
        fill_applicant(page, Applicant.from_dict(DATA))
        for (item, _), value in zip(fields, [DATA['name_kana'], DATA['phone'],
                                            DATA['certificate_number'], DATA['birth_date']]):
            item.fill.assert_called_once_with(value)
            item.click.assert_not_called()
        page.get_by_text.assert_not_called()

    def test_browser_error_does_not_disclose_fill_argument(self):
        fields = [control('氏名 カタカナ'), control('電話番号'),
                  control('卒業証明書等の番号'), control('生年月日', input_type='date')]
        page = MagicMock(url='https://license-test.tokyo-madoguchi-yoyaku.com/index.html')
        page.locator.return_value.all.return_value = [c for c, _ in fields]
        for item, info in fields:
            item.evaluate.return_value = info
        fields[0][0].fill.side_effect = RuntimeError('fill: ' + DATA['name_kana'])
        with self.assertRaises(FormError) as error:
            fill_applicant(page, Applicant.from_dict(DATA))
        self.assertNotIn(DATA['name_kana'], str(error.exception))


if __name__ == '__main__':
    unittest.main()
