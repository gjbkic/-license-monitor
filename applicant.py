"""Read applicant data without putting personal values in repr or error messages."""
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROFILE_PATH = Path(__file__).resolve().with_name('applicant.local.json')


class ProfileError(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class Applicant:
    name_kana: str = field(repr=False)
    birth_date: str = field(repr=False)
    phone: str = field(repr=False)
    certificate_number: str = field(repr=False)

    def __repr__(self):
        return 'Applicant(<redacted>)'

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ProfileError('申込情報はJSONオブジェクトで入力してください')
        names = ('name_kana', 'birth_date', 'phone', 'certificate_number')
        for name in names:
            if not isinstance(data.get(name), str) or not data[name].strip():
                raise ProfileError(f'{name} を文字列で入力してください')
        values = {name: data[name].strip() for name in names}
        if len(values['name_kana']) > 40:
            raise ProfileError('name_kana が長すぎます')
        try:
            born = date.fromisoformat(values['birth_date'])
        except ValueError:
            raise ProfileError('birth_date は実在する日付をYYYY-MM-DDで入力してください') from None
        if born.isoformat() != values['birth_date'] or born > date.today():
            raise ProfileError('birth_date を確認してください')
        if not re.fullmatch(r'[0-9]{10,11}', values['phone']):
            raise ProfileError('phone はハイフンなしの半角数字10〜11桁で入力してください')
        # 正式な証明書に記載された文字列を保持。サイト固有の桁数検証はサイト側で行う。
        if not re.fullmatch(r'[0-9A-Za-z-]{1,40}', values['certificate_number']):
            raise ProfileError('certificate_number の文字種を確認してください')
        return cls(**values)


def load_applicant():
    raw = os.environ.get('APPLICANT_JSON', '')
    if not raw:
        try:
            raw = PROFILE_PATH.read_text(encoding='utf-8')
        except OSError:
            raise ProfileError('APPLICANT_JSON または applicant.local.json を設定してください') from None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise ProfileError('APPLICANT_JSON のJSON形式を確認してください') from None
    return Applicant.from_dict(data)
