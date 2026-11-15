"""Separate per-user desktop configuration; encrypted blobs only, never plaintext."""
import os
from pathlib import Path
import sys

import protected_store as store


def configure():
    store.PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'LocalArentiBabyCam' / '.private'
    store.ACTIVE = store.PRIVATE / 'onvif.dpapi'
    store.JOURNAL = store.PRIVATE / 'onvif-setup.dpapi'
    return store.PRIVATE


def import_encrypted(source):
    """Used by the local installer; never overwrites an existing credential blob."""
    import json
    import win32crypt
    source = Path(source).resolve()
    files = [(source / name, store.PRIVATE / name) for name in ('onvif.dpapi', 'onvif-setup.dpapi')]
    # Validate both blobs for this Windows user before touching the destination.
    for original, destination in files:
        raw = original.read_bytes()
        if len(raw) > 65536:
            raise ValueError('Unexpected protected configuration size')
        _, plain = win32crypt.CryptUnprotectData(raw, store.ENTROPY, None, None, 0)
        obj = json.loads(plain)
        if not isinstance(obj, dict) or obj.get('state') != 'verified':
            raise ValueError('Verified protected configuration required')
        if destination.exists() and destination.read_bytes() != raw:
            raise ValueError('Existing desktop configuration differs; refusing replacement')
    store.PRIVATE.mkdir(parents=True, exist_ok=True)
    store.restrict_acl(store.PRIVATE, directory=True)
    for original, destination in files:
        if not destination.exists():
            # The protected parent ACL applies from creation, before data is written.
            with destination.open('xb') as target:
                target.write(original.read_bytes())
        store.restrict_acl(destination)
    return {'protectedConfigurationImported': True, 'plaintextSaved': False}
