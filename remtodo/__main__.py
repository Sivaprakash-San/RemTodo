import signal
import sys

from gi.repository import GLib

from .app import RemTodoApp


def main():
    app = RemTodoApp()
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (app.quit(), GLib.SOURCE_REMOVE)[1])
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
