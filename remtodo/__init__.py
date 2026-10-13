"""RemTodo: a tray todo list with deadlines, reminders and snooze for Linux desktops."""
import os

APP_ID = "io.github.remtodo.RemTodo"
APP_NAME = "RemTodo"

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(PKG_DIR)
ICON_DIR = os.path.join(PKG_DIR, "icons")
APP_ICON = os.path.join(ICON_DIR, "remtodo.svg")
LAUNCHER = os.path.join(ROOT_DIR, "bin", "remtodo")
