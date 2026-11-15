"""Read only the physical hold keys; never hooks, captures or injects input."""
import ctypes
import sys


def physical_hold_down():
    if sys.platform != 'win32':
        return False
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user.GetAsyncKeyState.restype = ctypes.c_short
    # Left mouse, Space or Enter. Only consulted during a focused arrow hold.
    return any(user.GetAsyncKeyState(key) & 0x8000 for key in (0x01, 0x20, 0x0D))
