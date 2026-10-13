"""Fires desktop notifications for due reminders, with Snooze / Done actions."""
import sys
import time

import gi

gi.require_version("Notify", "0.7")
from gi.repository import GLib, Notify  # noqa: E402

from . import APP_ICON, APP_ID, APP_NAME, timefmt  # noqa: E402

CHECK_INTERVAL = 10  # seconds; wall-clock polling stays correct across suspend/resume
SUMMARY_KEY = "__summary__"
MAX_INDIVIDUAL = 3  # more due at once (e.g. after being offline) -> one summary notification


class Reminders:
    def __init__(self, store, on_open):
        self.store = store
        self.on_open = on_open
        self._live = {}  # todo id -> Notify.Notification currently on screen
        Notify.init(APP_NAME)
        caps = Notify.get_server_caps() or []
        self._actions = "actions" in caps
        self._markup = "body-markup" in caps

    def start(self):
        self._tick()
        GLib.timeout_add_seconds(CHECK_INTERVAL, self._tick)

    def sync(self):
        """Withdraw notifications for todos that were completed, deleted, snoozed or rescheduled."""
        for todo_id, notification in list(self._live.items()):
            if todo_id == SUMMARY_KEY:
                continue
            todo = self.store.get(todo_id)
            if todo is None or todo.done or not todo.notified:
                self._close(todo_id)

    # -- internals --------------------------------------------------------

    def _tick(self):
        now = time.time()
        due = self.store.due_reminders(now)
        if due:
            for todo in due:
                todo.notified = True
            if len(due) > MAX_INDIVIDUAL:
                self._show_summary(due)
            else:
                for todo in due:
                    self._show(todo, now)
            self.store.changed()
        return True

    def _text(self, text):
        return GLib.markup_escape_text(text) if self._markup else text

    def _new(self, summary, body, key):
        notification = Notify.Notification.new(summary, self._text(body), APP_ICON)
        notification.set_urgency(Notify.Urgency.CRITICAL)  # stays on screen until handled
        notification.set_hint("desktop-entry", GLib.Variant("s", APP_ID))
        notification.set_category("x-gnome.reminder")
        if self.store.settings.get("sound", True):
            notification.set_hint("sound-name", GLib.Variant("s", "alarm-clock-elapsed"))
        else:
            notification.set_hint("suppress-sound", GLib.Variant("b", True))
        notification.connect("closed", self._on_closed, key)
        return notification

    def _show(self, todo, now):
        lines = []
        if todo.deadline is not None:
            if todo.deadline > now + 59:
                lines.append(f"Due {timefmt.when(todo.deadline, now)} ({timefmt.relative(todo.deadline, now)})")
            elif todo.deadline > now - 60:
                lines.append("Due now")
            else:
                lines.append(f"Overdue by {timefmt.duration(now - todo.deadline)} "
                             f"(was due {timefmt.when(todo.deadline, now)})")
        if todo.notes.strip():
            lines.append(todo.notes.strip().splitlines()[0])

        notification = self._new(todo.title, "\n".join(lines), todo.id)
        if self._actions:
            notification.add_action("default", "Open", self._on_action, todo.id)
            if todo.snooze_minutes:
                label = f"Snooze {timefmt.duration(todo.snooze_minutes * 60)}"
                notification.add_action("snooze", label, self._on_action, todo.id)
            notification.add_action("done", "Mark done", self._on_action, todo.id)
        self._present(todo.id, notification)

    def _show_summary(self, todos):
        titles = [f"• {t.title}" for t in todos[:5]]
        if len(todos) > 5:
            titles.append(f"…and {len(todos) - 5} more")
        notification = self._new(f"{len(todos)} reminders due", "\n".join(titles), SUMMARY_KEY)
        if self._actions:
            notification.add_action("default", "Open", self._on_action, SUMMARY_KEY)
            notification.add_action("open", "Show todos", self._on_action, SUMMARY_KEY)
        self._present(SUMMARY_KEY, notification)

    def _present(self, key, notification):
        self._close(key)
        self._live[key] = notification  # keep a reference or action callbacks are lost
        try:
            notification.show()
        except GLib.Error as e:
            print(f"remtodo: could not show notification: {e.message}", file=sys.stderr)

    def _close(self, key):
        notification = self._live.pop(key, None)
        if notification is not None:
            try:
                notification.close()
            except GLib.Error:
                pass

    def _on_closed(self, notification, key):
        if self._live.get(key) is notification:
            del self._live[key]

    def _on_action(self, _notification, action, todo_id):
        self._live.pop(todo_id, None)
        todo = self.store.get(todo_id)
        if action in ("default", "open") or todo is None:
            self.on_open()
        elif action == "snooze":
            if todo.snooze():
                self.store.changed()
        elif action == "done":
            todo.set_done(True)
            self.store.changed()
