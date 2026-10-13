"""Desktop integration: launcher entry, icon and start-on-login."""
import os
import shutil

from . import APP_ICON, APP_ID, APP_NAME, LAUNCHER


def _config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def _data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


AUTOSTART_FILE = os.path.join(_config_home(), "autostart", f"{APP_ID}.desktop")
APPLICATION_FILE = os.path.join(_data_home(), "applications", f"{APP_ID}.desktop")
ICON_FILE = os.path.join(_data_home(), "icons", "hicolor", "scalable", "apps", f"{APP_ID}.svg")
BIN_LINK = os.path.expanduser("~/.local/bin/remtodo")


def desktop_entry(*args):
    icon = APP_ID if os.path.exists(ICON_FILE) else APP_ICON
    exec_line = " ".join([f'"{LAUNCHER}"', *args])
    return f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
GenericName=Todos & Reminders
Comment=Todos with deadlines, reminders and snooze
Exec={exec_line}
Icon={icon}
Terminal=false
Categories=Utility;GTK;
Keywords=todo;task;reminder;alarm;deadline;
StartupNotify=true
X-GNOME-UsesNotifications=true
Actions=new-todo;

[Desktop Action new-todo]
Name=New Todo
Exec="{LAUNCHER}" --new
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def autostart_enabled():
    return os.path.exists(AUTOSTART_FILE)


def set_autostart(enabled):
    if enabled:
        _write(AUTOSTART_FILE, desktop_entry("--hidden") + "X-GNOME-Autostart-enabled=true\n")
    elif os.path.exists(AUTOSTART_FILE):
        os.remove(AUTOSTART_FILE)


def install():
    os.makedirs(os.path.dirname(ICON_FILE), exist_ok=True)
    shutil.copyfile(APP_ICON, ICON_FILE)
    _write(APPLICATION_FILE, desktop_entry())
    set_autostart(True)
    os.makedirs(os.path.dirname(BIN_LINK), exist_ok=True)
    if os.path.islink(BIN_LINK) or not os.path.exists(BIN_LINK):
        if os.path.islink(BIN_LINK):
            os.remove(BIN_LINK)
        os.symlink(LAUNCHER, BIN_LINK)
    return [APPLICATION_FILE, AUTOSTART_FILE, ICON_FILE, BIN_LINK]


def uninstall():
    removed = []
    for path in (APPLICATION_FILE, AUTOSTART_FILE, ICON_FILE):
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)
    if os.path.islink(BIN_LINK) and os.readlink(BIN_LINK) == LAUNCHER:
        os.remove(BIN_LINK)
        removed.append(BIN_LINK)
    return removed
