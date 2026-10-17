"""Find each venue's first available date, then order candidates globally."""
import re
from datetime import timedelta
import monitor


def slot_key(slot):
    match = re.search(r'(\d{1,2}):(\d{2})', slot['time'])
    minutes = int(match[1]) * 60 + int(match[2]) if match else (0 if '午前' in slot['time'] else 720)
    return slot['date'], minutes, monitor.LOCATIONS.index(slot['location'])


def first_at_location(browser, location, today, end):
    context = monitor.create_context(browser)
    page = context.new_page()
    guard = monitor.AccessGuard()
    page.on('response', guard.on_response)
    try:
        monitor.setup_calendar(page, guard, location)
        target = today + timedelta(days=1)
        while target <= end:
            slots = monitor.inspect_date(page, guard, target)
            if slots:
                return [{**slot, 'location': location} for slot in slots]
            target += timedelta(days=1)
        return []
    except monitor.AccessBlocked:
        raise
    except Exception:
        raise monitor.ScanError(location + 'の最短日確認が完了しませんでした。空きなしとは判定していません。') from None
    finally:
        context.close()


def earliest_candidates(browser, today, end):
    candidates = []
    # A later date at the first venue must never outrank an earlier one elsewhere.
    for location in monitor.LOCATIONS:
        candidates.extend(first_at_location(browser, location, today, end))
    return sorted(candidates, key=slot_key)
