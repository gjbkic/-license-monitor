"""Interactive local setup. Personal data is never echoed to the terminal."""
import getpass
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from applicant import Applicant, ProfileError, PROFILE_PATH


LABELS = {
    'name_kana': '氏名（全角カナ又はアルファベット・40文字以内）',
    'birth_date': '生年月日（YYYY-MM-DD）',
    'phone': '電話番号（ハイフンなし・10〜11桁）',
    'certificate_number': '卒業証明書等の番号（予約後の受付番号ではありません）',
}


def profile_json(data):
    profile = Applicant.from_dict(data)
    return json.dumps({key: getattr(profile, key) for key in LABELS}, ensure_ascii=False, indent=2)


def save_profile(data, target=PROFILE_PATH):
    content = profile_json(data)  # Validate before opening or replacing any file.
    target = Path(target)
    fd, temp_name = tempfile.mkstemp(prefix='.applicant-', suffix='.tmp', dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def gui():
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError:
        raise ProfileError('このPythonにはTkinterがありません。--guiを付けずに端末入力してください。') from None
    root = tk.Tk()
    root.title('本免予約・本人情報の登録')
    root.minsize(640, 370)
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='本人の正式な情報を入力してください', font=('', 14, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='この画面は予約サイトへ送信しません。保存だけで予約は始まりません。').pack(anchor='w', pady=(6, 12))
    inputs = {}
    for key, label in LABELS.items():
        ttk.Label(frame, text=label).pack(anchor='w', pady=(6, 2))
        inputs[key] = tk.StringVar()
        ttk.Entry(frame, textvariable=inputs[key], width=65).pack(fill='x')

    def data():
        return {key: variable.get() for key, variable in inputs.items()}

    def save():
        try:
            profile_json(data())
            if PROFILE_PATH.exists() and not messagebox.askyesno('保存の確認', '保存済みの本人情報を上書きしますか？', parent=root):
                return
            save_profile(data())
        except ProfileError as error:
            messagebox.showerror('入力を確認してください', str(error), parent=root)
            return
        except OSError:
            messagebox.showerror('保存できません', 'このフォルダへの書込権限を確認してください。', parent=root)
            return
        messagebox.showinfo('保存しました', '本人情報を保存しました。予約処理は開始していません。', parent=root)

    def copy():
        try:
            content = profile_json(data())
        except ProfileError as error:
            messagebox.showerror('入力を確認してください', str(error), parent=root)
            return
        root.clipboard_clear()
        root.clipboard_append(content)
        messagebox.showinfo('コピーしました', 'GitHubのAPPLICANT_JSON SecretのValue欄へ貼り付けてください。\n本人情報を含むため、チャットやコードには貼らないでください。\n貼り付けが終わるまでこの画面を開いておいてください。', parent=root)

    buttons = ttk.Frame(frame)
    buttons.pack(fill='x', pady=(18, 10))
    ttk.Button(buttons, text='このPCに保存', command=save).pack(side='left')
    ttk.Button(buttons, text='GitHub用JSONをコピー', command=copy).pack(side='left', padx=10)
    ttk.Label(frame, text='保存先: ' + str(PROFILE_PATH), wraplength=600).pack(anchor='w')
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description='本人情報を手元で登録（予約は開始しません）')
    parser.add_argument('--gui', action='store_true', help='入力画面を開く')
    args = parser.parse_args()
    if args.gui:
        gui()
        return
    if not sys.stdin.isatty():
        raise ProfileError('入力内容を非表示にできる端末で実行してください')
    print('本人の正式な情報を入力してください。入力文字は画面に表示されません。')
    data = {key: getpass.getpass(label + ': ') for key, label in LABELS.items()}
    Applicant.from_dict(data)
    target = PROFILE_PATH
    if target.exists() and input('既存のローカル申込情報を上書きしますか？ [y/N] ') != 'y':
        return
    save_profile(data, target)
    print('申込情報を保存しました。値はログに表示しません。')


if __name__ == '__main__':
    try:
        main()
    except (ProfileError, EOFError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, ProfileError) else '入力を中断しました')
        raise SystemExit(1)
