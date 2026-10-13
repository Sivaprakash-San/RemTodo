"""Application: single instance, tray icon, reminders and the main window."""
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, ICON_DIR, desktop, timefmt  # noqa: E402
from .reminders import Reminders  # noqa: E402
from .store import Store  # noqa: E402
from .tray import TrayIcon  # noqa: E402
from .window import MainWindow  # noqa: E402

TRAY_TODOS = 6
UI_REFRESH_SECONDS = 30


class RemTodoApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("hidden", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start in the tray without opening the window", None)
        self.add_main_option("new", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open the new-todo dialog", None)
        self.add_main_option("quit", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Quit the running instance", None)
        self.store = None
        self.window = None
        self.tray = None
        self.reminders = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(ICON_DIR)
        timefmt.configure_from_desktop()

        self.store = Store()
        self.store.load()
        self._add_actions()
        self.reminders = Reminders(self.store, on_open=self.show_window)
        self.window = MainWindow(self)
        self.tray = TrayIcon(APP_ID, ICON_DIR, "remtodo-tray-symbolic", "remtodo-tray-alert-symbolic",
                             on_activate=self.toggle_window, on_secondary_activate=self.new_todo)
        self.store.connect(self._on_store_changed)
        self._refresh_tray()
        self.reminders.start()
        GLib.timeout_add_seconds(UI_REFRESH_SECONDS, self._on_timer)
        self.hold()  # keep running with the window hidden

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        if options.contains("quit"):
            self.quit()
        elif options.contains("new"):
            self.new_todo()
        elif not options.contains("hidden"):
            self.show_window()
        return 0

    # -- actions ----------------------------------------------------------

    def _add_actions(self):
        def simple(name, callback, accels=()):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_: callback())
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", list(accels))

        def toggle(name, value, on_change):
            action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", value))

            def changed(act, state):
                act.set_state(state)
                on_change(state.get_boolean())
            action.connect("change-state", changed)
            self.add_action(action)

        simple("new", self.new_todo, ["<Primary>n"])
        simple("show", self.show_window)
        simple("about", self._show_about)
        simple("quit", self.quit, ["<Primary>q"])
        toggle("sound", self.store.settings.get("sound", True), self._set_sound)
        toggle("autostart", desktop.autostart_enabled(), desktop.set_autostart)

    def build_menu(self):
        menu = Gio.Menu()
        prefs = Gio.Menu()
        prefs.append("Play sound with reminders", "app.sound")
        prefs.append("Start on login", "app.autostart")
        menu.append_section(None, prefs)
        quit_section = Gio.Menu()
        quit_section.append("About RemTodo", "app.about")
        quit_section.append("Quit", "app.quit")
        menu.append_section(None, quit_section)
        return menu

    def _set_sound(self, enabled):
        self.store.settings["sound"] = enabled
        self.store.save()

    def _show_about(self):
        about = Adw.AboutDialog(application_name=APP_NAME, application_icon="remtodo",
                                comments="Todos with deadlines, reminders and snooze, right in your tray.",
                                version="1.1")
        self.window.show_and_focus()
        about.present(self.window)

    def show_window(self):
        self.window.show_and_focus()

    def toggle_window(self):
        self.window.toggle()

    def new_todo(self):
        self.window.show_and_focus()
        self.window.open_editor()

    def edit_todo(self, todo_id):
        todo = self.store.get(todo_id)
        self.window.show_and_focus()
        if todo is not None:
            self.window.open_editor(todo)

    # -- updates ----------------------------------------------------------

    def _on_store_changed(self):
        self.window.refresh()
        self.reminders.sync()
        self._refresh_tray()

    def _on_timer(self):
        if self.window.get_visible():
            self.window.refresh()  # keeps "in 5 min" / overdue colouring current
        self._refresh_tray()
        return True

    def _refresh_tray(self):
        now = time.time()
        pending = self.store.pending()
        overdue = [t for t in pending if t.is_overdue(now)]

        if not pending:
            tooltip = "Nothing to do"
        else:
            tooltip = f"{len(pending)} to do" + (f", {len(overdue)} overdue" if overdue else "")
            upcoming = [t for t in pending if t.deadline is not None and not t.is_overdue(now)]
            if upcoming:
                tooltip += f"\nNext: {upcoming[0].title} · {timefmt.when(upcoming[0].deadline, now)}"
        self.tray.set_status(title=APP_NAME, tooltip=tooltip, attention=bool(overdue))

        items = [("Open RemTodo", self.show_window), ("New todo…", self.new_todo), None]
        if not pending:
            items.append(("Nothing to do", None))
        for todo in pending[:TRAY_TODOS]:
            label = todo.title
            if todo.deadline is not None:
                when = "overdue" if todo.is_overdue(now) else timefmt.when(todo.deadline, now)
                label = f"{label}  —  {when}"
            items.append((label, lambda todo_id=todo.id: self.edit_todo(todo_id)))
        if len(pending) > TRAY_TODOS:
            items.append((f"…and {len(pending) - TRAY_TODOS} more", self.show_window))
        items += [None, ("Quit", self.quit)]
        self.tray.set_menu(items)
