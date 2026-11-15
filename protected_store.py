"""Current-Windows-user DPAPI storage; never writes plaintext credentials."""
import json
import os
from pathlib import Path

PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'LocalArentiBabyCam' / '.private'
ACTIVE = PRIVATE / 'onvif.dpapi'
JOURNAL = PRIVATE / 'onvif-setup.dpapi'
ENTROPY = b'TVModding-Arenti-ONVIF-v1'


def restrict_acl(path, directory=False):
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    flags = (win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE) if directory else 0
    for allowed in (sid, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)):
        acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, win32con.GENERIC_ALL, allowed)
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)


def save(path, value):
    import win32crypt
    if path not in (ACTIVE, JOURNAL):
        raise ValueError('Unapproved credential destination')
    PRIVATE.mkdir(parents=True, exist_ok=True)
    restrict_acl(PRIVATE, directory=True)
    plain = json.dumps(value, separators=(',', ':')).encode()
    encrypted = win32crypt.CryptProtectData(plain, 'Arenti local ONVIF', ENTROPY, None, None, 0)
    temp = path.with_suffix('.dpapi-new')
    if temp.exists():
        raise RuntimeError('Previous encrypted credential write needs review')
    temp.write_bytes(encrypted)
    restrict_acl(temp)
    os.replace(temp, path)
    restrict_acl(path)


def load(path=None):
    import win32crypt
    if path is None:
        path = ACTIVE
    if path not in (ACTIVE, JOURNAL):
        raise ValueError('Unapproved credential source')
    _description, plain = win32crypt.CryptUnprotectData(path.read_bytes(), ENTROPY, None, None, 0)
    result = json.loads(plain)
    if not isinstance(result, dict):
        raise ValueError('Unexpected credential container')
    return result
