"""Per-user source installation shortcuts; isolated from other camera apps."""
import os
from pathlib import Path
import sys


def available():
    return (Path(__file__).parent / '.local-arenti-install').is_file()


def startup_path():
    return Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Startup/Local Arenti Baby Cam.lnk'


def executable_path():
    if not available():
        raise ValueError('Run Install-Windows.ps1 first')
    return Path(sys.executable).with_name('pythonw.exe')


def arguments():
    return '"' + str(Path(__file__).parent / 'desktop_app.py') + '" --background'


def shortcut():
    import win32com.client
    return win32com.client.Dispatch('WScript.Shell').CreateShortcut(str(startup_path()))


def owned(link):
    return (os.path.normcase(link.TargetPath) == os.path.normcase(str(executable_path()))
        and link.Arguments == arguments())


def enabled():
    return startup_path().exists() and owned(shortcut())


def set_enabled(value):
    executable = executable_path()
    path = startup_path()
    link = shortcut()
    if path.exists() and not owned(link):
        raise ValueError('An unrelated startup shortcut already uses this name')
    if not value:
        if path.exists():
            path.unlink()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        link.TargetPath = str(executable)
        link.Arguments = arguments()
        link.WorkingDirectory = str(Path(__file__).parent)
        link.Description = 'Local Arenti Baby Cam — tray startup, preview idle'
        link.Save()
    return {'startWithWindows': bool(value), 'cameraAutoStart': False, 'startsInTray': True}
