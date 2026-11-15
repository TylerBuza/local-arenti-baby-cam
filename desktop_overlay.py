"""Display-only Windows clock and source freshness; never changes media or polls."""
from datetime import datetime
import time

from owlet_overlay import age_snapshot, timestamp


def video_clock(last_frame, now=None, wall_now=None):
    now = time.monotonic() if now is None else now
    if last_frame is None or not 0 <= now - last_frame < 1.25:
        return ''
    wall_now = time.time() if wall_now is None else wall_now
    return 'PC ' + datetime.fromtimestamp(wall_now).strftime('%Y-%m-%d %H:%M:%S')


def owlet_freshness(value, now=None):
    now = time.time() if now is None else now
    if value is None:
        return ''
    current = age_snapshot(value, now)
    state = current.get('availability')
    label = {'live': 'Owlet', 'offline': 'Sock offline', 'stale': 'Owlet stale',
             'loading': 'Owlet loading', 'unconfigured': 'Owlet not configured'}.get(state, 'Owlet unavailable')
    try:
        age = now - timestamp(current.get('updated_at'))
        if age < -10:
            return label + ' · source time ahead'
        seconds = max(0, int(age))
        elapsed = f'{seconds}s' if seconds < 60 else f'{seconds // 60}m' if seconds < 3600 else f'{seconds // 3600}h'
        return label + ' · updated ' + elapsed + ' ago'
    except (ValueError, TypeError, OverflowError):
        return label
