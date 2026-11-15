"""Normalized phone overlay data; private widget address stays on the home PC."""
import time
from owlet_overlay import StatusPoller, age_snapshot, empty_snapshot, timestamp


def make_poller():
    from owlet_windows import load_address
    return StatusPoller(load_address)


def phone_snapshot(value):
    value = age_snapshot(value)
    result = {key: value.get(key) for key in empty_snapshot() if key != 'updated_at'}
    try:
        age = max(0, time.time() - timestamp(value.get('updated_at')))
    except (ValueError, TypeError, OverflowError):
        age = None
    result['sourceAgeSeconds'] = age
    return result
