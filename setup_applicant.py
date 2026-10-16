"""Interactive local setup. Personal data is never echoed to the terminal."""
import getpass
import json
import os
import sys
from pathlib import Path
from applicant import Applicant, ProfileError


def main():
    if not sys.stdin.isatty():
        raise ProfileError('入力内容を非表示にできる端末で実行してください')
    print('本人の正式な情報を入力してください。入力文字は画面に表示されません。')
    labels = {
        'name_kana': '氏名（全角カタカナ・サイト指定の表記）: ',
        'birth_date': '生年月日（YYYY-MM-DD）: ',
        'phone': '電話番号（ハイフンなし）: ',
        'certificate_number': '卒業証明書等の番号（予約後の受付番号ではありません）: ',
    }
    data = {key: getpass.getpass(label) for key, label in labels.items()}
    Applicant.from_dict(data)
    target = Path('applicant.local.json')
    if target.exists() and input('既存のローカル申込情報を上書きしますか？ [y/N] ') != 'y':
        return
    # Windowsではファイルのアクセス権はWindowsのユーザーフォルダ設定に従います。
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        if os.name != 'nt':
            os.fchmod(stream.fileno(), 0o600)
        json.dump(data, stream, ensure_ascii=False, indent=2)
    print('申込情報を保存しました。値はログに表示しません。')


if __name__ == '__main__':
    try:
        main()
    except (ProfileError, EOFError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, ProfileError) else '入力を中断しました')
        raise SystemExit(1)
