import json
import os
from pathlib import Path
import tempfile
import unittest

from applicant import ProfileError
from setup_applicant import save_profile


class SetupTests(unittest.TestCase):
    def test_save_preserves_zeroes_and_rejects_bad_data_without_overwrite(self):
        data = {'name_kana': 'テストタロウ', 'birth_date': '2000-01-02',
                'phone': '0312345678', 'certificate_number': '0000123'}
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'applicant.local.json'
            save_profile(data, target)
            self.assertEqual(json.loads(target.read_text())['certificate_number'], '0000123')
            before = target.read_bytes()
            with self.assertRaises(ProfileError):
                save_profile({**data, 'birth_date': '2000-02-31'}, target)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(list(Path(folder).glob('.applicant-*')), [])
            if os.name != 'nt':
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
