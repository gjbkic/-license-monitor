"""Shared browser identification for the booking site."""
import os


# This exact value returned HTTP 200 in the user's Windows curl test.
# It is a compatibility setting, not proof that cloud access works.
DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/155.0.0.0 Safari/537.36'
)


def context_options():
    user_agent = os.environ.get('BOOKING_USER_AGENT', '').strip() or DEFAULT_USER_AGENT
    if '\r' in user_agent or '\n' in user_agent:
        raise ValueError('BOOKING_USER_AGENT must be a single line')
    return {'locale': 'ja-JP', 'user_agent': user_agent}
