"""Keep checking on 300-second start deadlines within one Actions runner.

The cron event only wakes a runner. It is not the clock for individual scans.
Booking state remains owned by auto_reserve.py; no second submission is added.
"""
import argparse
import base64
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import auto_reserve as booking
import monitor
from applicant import load_applicant

INTERVAL = 300
CLOCK_PATH = Path('.monitor_loop.json')
CONTROL_PATH = Path('monitor_control.json')


def control_enabled():
    """Read the current main-branch stop switch, even during a long job."""
    try:
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            repository = os.environ['GITHUB_REPOSITORY']
            token = os.environ['GH_CONTROL_TOKEN']
            response = requests.get(
                'https://api.github.com/repos/' + repository + '/contents/monitor_control.json',
                params={'ref': 'main'},
                headers={'Authorization': 'Bearer ' + token,
                         'Accept': 'application/vnd.github+json'}, timeout=15)
            response.raise_for_status()
            control = json.loads(base64.b64decode(response.json()['content']))
        else:
            control = json.loads(CONTROL_PATH.read_text(encoding='utf-8'))
        if type(control.get('enabled')) is not bool or control.get('interval_seconds') != INTERVAL:
            raise ValueError()
        return control['enabled']
    except Exception:
        # Never expose request URLs, headers or credentials through exceptions.
        print('停止設定を確認できません。この回の予約操作を見送り、5分後に再確認します。', flush=True)
        return None


def read_deadline(now):
    if not CLOCK_PATH.exists():
        return now
    try:
        value = json.loads(CLOCK_PATH.read_text(encoding='utf-8'))['next_check_at']
        if not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError()
        # An overdue handoff checks immediately. A stale future clock never
        # delays the first check by more than one normal interval.
        return min(max(float(value), now), now + INTERVAL)
    except Exception:
        return now


def save_deadline(value):
    temporary = CLOCK_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps({'next_check_at': value}), encoding='utf-8')
    temporary.replace(CLOCK_PATH)


def next_deadline(previous, now):
    value = previous + INTERVAL
    if value < now:
        # Slow scans never cause a burst of catch-up submissions.
        value += math.ceil((now - value) / INTERVAL) * INTERVAL
    return value


def run_cycle():
    try:
        result = subprocess.run([sys.executable, '-u', 'auto_reserve.py'],
                                timeout=900, check=False)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print('今回の確認は15分で打ち切りました。予約送信状態を確認します。', flush=True)
        return False


def write_handoff(keep_running):
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8') as stream:
            stream.write('continue=' + ('true' if keep_running else 'false') + '\n')


def run_loop(duration=330 * 60, max_cycles=0, clock=time.time, sleep=time.sleep,
             check=run_cycle, control=control_enabled):
    started = clock()
    due = read_deadline(started)
    cycles = 0
    write_handoff(False)
    while clock() < started + duration:
        # Short sleeps allow prompt process cancellation; scan deadlines are
        # measured from starts, rather than scan time plus another five minutes.
        if clock() < due:
            sleep(min(30, due - clock(), started + duration - clock()))
            continue
        state = booking.read_booking_state()
        if state['status'] == 'submitting':
            print('予約送信結果が未確認のため、再送せず連続確認を停止します。', flush=True)
            return 1
        if state['status'] == 'booked' and state.get('notified'):
            print('予約取得・通知済みのため、連続確認を終了します。', flush=True)
            return 0
        today = datetime.now(ZoneInfo('Asia/Tokyo')).date()
        if today >= monitor.monitor_end_date(today):
            print('予約対象期間が終了したため、連続確認を終了します。', flush=True)
            return 0
        enabled = control()
        if enabled is False:
            print('停止指示を確認しました。連続確認を終了します。', flush=True)
            return 0
        if enabled is True:
            stamp = datetime.fromtimestamp(clock(), ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds')
            print('5分間隔の確認開始: ' + stamp, flush=True)
            okay = check()
            print('今回の確認終了: ' + ('正常' if okay else 'エラー（空きなしとは判定しません）'), flush=True)
            state = booking.read_booking_state()
            if state['status'] == 'submitting':
                print('予約送信結果が未確認のため、再送せず連続確認を停止します。', flush=True)
                return 1
            if state['status'] == 'booked' and state.get('notified'):
                print('予約取得・通知済みのため、連続確認を終了します。', flush=True)
                return 0
        cycles += 1
        due = next_deadline(due, clock())
        save_deadline(due)
        if max_cycles and cycles >= max_cycles:
            write_handoff(True)
            return 0
    write_handoff(True)
    print('今回の連続実行時間が終了しました。次のジョブへ状態を引き継ぎます。', flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-cycles', type=int, default=0)
    args = parser.parse_args()
    # Permanent configuration failures stop before starting a long retry loop.
    if os.environ.get('AUTO_RESERVE_ENABLED') != 'true' or os.environ.get('BOOKING_TERMS_ACCEPTED') != 'true':
        print('自動予約または利用規約への同意が無効のため停止します。')
        return 1
    if not os.environ.get('DISCORD_WEBHOOK'):
        print('Discord通知先が未設定のため停止します。')
        return 1
    load_applicant()
    return run_loop(max_cycles=args.max_cycles)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('連続確認を停止しました。個人情報を含む詳細は表示しません。', flush=True)
        raise SystemExit(1)
