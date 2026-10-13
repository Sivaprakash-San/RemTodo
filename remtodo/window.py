"""Main window: todos grouped by when they're due, quick add, and a completed section."""
import os
import time
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import APP_NAME, timefmt  # noqa: E402
from .editor import EditorDialog, open_file  # noqa: E402
from .store import Todo  # noqa: E402

CSS = """
.hero-day { font-size: 1.6em; font-weight: 800; }
.section-title { font-weight: 700; font-size: 0.92em; margin-top: 6px; }
.section-count { font-size: 0.85em; }
.todo-row { padding: 10px 6px 10px 12px; }
.todo-title { font-weight: 600; }
.chip {
  font-size: 0.82em; font-weight: 600;
  padding: 2px 9px; border-radius: 999px;
  background-color: alpha(currentColor, 0.08);
}
.chip.overdue { color: @error_color; background-color: alpha(@error_color, 0.14); }
.chip.soon { color: @warning_color; background-color: alpha(@warning_color, 0.16); }
.chip.accent { color: @accent_color; background-color: alpha(@accent_bg_color, 0.14); }
.snooze-button {
  padding: 2px 12px; min-height: 26px; border-radius: 999px; font-weight: 600;
  color: @accent_color; background-color: alpha(@accent_bg_color, 0.14);
}
.snooze-button:hover { background-color: alpha(@accent_bg_color, 0.22); }
.chip-button { border-radius: 999px; padding: 4px 12px; }
.notes-view { background: none; }
.todo-notes { font-size: 0.9em; }
"""

UNDO_SECONDS = 6
SOON_SECONDS = 3600
DONE_DELAY_MS = 350  # let the check animation play before the row moves


def _later(callback, *args, delay=0):
    """Run after the current handler, so handlers may rebuild the rows they live in."""
    def run():
        callback(*args)
        return False
    if delay:
        GLib.timeout_add(delay, run)
    else:
        GLib.idle_add(run)


class WrapLayout(Gtk.LayoutManager):
    """Lays children out left to right, wrapping onto new lines like words in a paragraph."""

    def __init__(self, spacing=6, row_spacing=4):
        super().__init__()
        self.spacing = spacing
        self.row_spacing = row_spacing

    @staticmethod
    def _children(widget):
        child = widget.get_first_child()
        while child is not None:
            if child.should_layout():
                yield child
            child = child.get_next_sibling()

    def _lines(self, widget, width):
        """Yield lists of (child, w, h) per line for the given width."""
        line, used = [], 0
        for child in self._children(widget):
            min_w, nat_w, _, _ = child.measure(Gtk.Orientation.HORIZONTAL, -1)
            w = max(min_w, min(nat_w, width))
            _, nat_h, _, _ = child.measure(Gtk.Orientation.VERTICAL, w)
            if line and used + self.spacing + w > width:
                yield line
                line, used = [], 0
            used += (self.spacing if line else 0) + w
            line.append((child, w, nat_h))
        if line:
            yield line

    def do_get_request_mode(self, _widget):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, widget, orientation, for_size):
        children = list(self._children(widget))
        if orientation == Gtk.Orientation.HORIZONTAL:
            sizes = [c.measure(orientation, -1) for c in children]
            minimum = max((m[0] for m in sizes), default=0)
            natural = sum(m[1] for m in sizes) + self.spacing * max(len(sizes) - 1, 0)
            return minimum, natural, -1, -1
        width = for_size if for_size >= 0 else 1 << 20
        heights = [max(h for _, _, h in line) for line in self._lines(widget, width)]
        total = sum(heights) + self.row_spacing * max(len(heights) - 1, 0)
        return total, total, -1, -1

    def do_allocate(self, widget, width, _height, _baseline):
        y = 0
        for line in self._lines(widget, width):
            x, line_h = 0, max(h for _, _, h in line)
            for child, w, h in line:
                rect = Gdk.Rectangle()
                rect.x, rect.y, rect.width, rect.height = x, y + (line_h - h) // 2, w, h
                child.size_allocate(rect, -1)
                x += w + self.spacing
            y += line_h + self.row_spacing


def _chip(icon, text, css=None, tooltip=None):
    box = Gtk.Box(spacing=5, valign=Gtk.Align.CENTER, tooltip_text=tooltip)
    box.add_css_class("chip")
    if css:
        box.add_css_class(css)
    box.append(Gtk.Image.new_from_icon_name(icon))
    box.append(Gtk.Label(label=text, ellipsize=Pango.EllipsizeMode.END))
    return box


def _section_of(todo, now):
    if todo.deadline is None:
        return "someday"
    if todo.deadline <= now:
        return "overdue"
    days = (datetime.fromtimestamp(todo.deadline).date() - datetime.fromtimestamp(now).date()).days
    return "today" if days == 0 else "tomorrow" if days == 1 else "upcoming"


SECTIONS = [
    ("overdue", "Overdue", "error"),
    ("today", "Today", None),
    ("tomorrow", "Tomorrow", None),
    ("upcoming", "Upcoming", None),
    ("someday", "No deadline", None),
]


class TodoRow(Gtk.ListBoxRow):
    def __init__(self, win, todo, now):
        super().__init__(activatable=not todo.done)
        self.todo = todo
        self._win = win
        box = Gtk.Box(spacing=12)
        box.add_css_class("todo-row")

        check = Gtk.CheckButton(active=todo.done, valign=Gtk.Align.CENTER,
                                tooltip_text="Mark as not done" if todo.done else "Mark as done")
        check.add_css_class("selection-mode")  # round check
        check.connect("toggled", lambda b: _later(win.set_done, todo, b.get_active(), delay=DONE_DELAY_MS))
        box.append(check)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True, valign=Gtk.Align.CENTER)
        title = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        escaped = GLib.markup_escape_text(todo.title)
        title.set_markup(f"<s>{escaped}</s>" if todo.done else escaped)
        title.add_css_class("dim-label" if todo.done else "todo-title")
        text.append(title)
        if todo.notes and not todo.done:
            notes = Gtk.Label(label=todo.notes.splitlines()[0], xalign=0, ellipsize=Pango.EllipsizeMode.END)
            notes.add_css_class("todo-notes")
            notes.add_css_class("dim-label")
            text.append(notes)
        chips = self._chips(todo, now)
        if todo.reminder_fired() and todo.snooze_minutes:
            snooze = Gtk.Button(child=Adw.ButtonContent(
                icon_name="alarm-symbolic", label=f"Snooze {timefmt.duration(todo.snooze_minutes * 60)}"),
                valign=Gtk.Align.CENTER, tooltip_text="Remind me again later")
            snooze.add_css_class("snooze-button")
            snooze.connect("clicked", lambda _b: _later(win.snooze, todo))
            chips.append(snooze)
        if chips:
            wrap = Gtk.Box(layout_manager=WrapLayout())
            for chip in chips:
                wrap.append(chip)
            text.append(wrap)
        box.append(text)

        menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER,
                                     menu_model=self._menu(todo), tooltip_text="More")
        menu_button.add_css_class("flat")
        box.append(menu_button)
        self.insert_action_group("todo", self._actions(todo))
        self.set_child(box)

    @staticmethod
    def _chips(todo, now):
        chips = []
        if todo.done:
            if todo.done_at:
                chips.append(_chip("object-select-symbolic", f"Done {timefmt.when(todo.done_at, now)}"))
        else:
            if todo.deadline is not None:
                left = todo.deadline - now
                css = "overdue" if left <= 0 else "soon" if left < SOON_SECONDS else None
                chips.append(_chip("x-office-calendar-symbolic",
                                   f"{timefmt.short_when(todo.deadline, now)} · {timefmt.relative(todo.deadline, now)}",
                                   css, tooltip=f"Due {timefmt.when(todo.deadline, now)}"))
            if todo.remind_at is not None and not todo.notified:
                same_day = (todo.deadline is not None
                            and timefmt.day(todo.remind_at, now) == timefmt.day(todo.deadline, now))
                when = timefmt.clock(todo.remind_at) if same_day else timefmt.short_when(todo.remind_at, now)
                label = f"Snoozed till {when}" if todo.snoozed else when
                chips.append(_chip("alarm-symbolic", label, "accent",
                                   tooltip=f"Reminder {timefmt.when(todo.remind_at, now)}"))
        if todo.attachments:
            n = len(todo.attachments)
            chips.append(_chip("mail-attachment-symbolic", f"{n} file{'s' if n > 1 else ''}",
                               tooltip="\n".join(a["name"] for a in todo.attachments)))
        return chips

    def _menu(self, todo):
        menu = Gio.Menu()
        main = Gio.Menu()
        if not todo.done:
            main.append("Edit…", "todo.edit")
            if todo.reminder_fired() and todo.snooze_minutes:
                main.append(f"Snooze {timefmt.duration(todo.snooze_minutes * 60)}", "todo.snooze")
        main.append("Mark as not done" if todo.done else "Mark as done", "todo.toggle")
        menu.append_section(None, main)
        if todo.attachments:
            files = Gio.Menu()
            for i, attachment in enumerate(todo.attachments[:8]):
                files.append(f"Open “{attachment['name']}”", f"todo.open-file({i})")
            menu.append_section(None, files)
        danger = Gio.Menu()
        danger.append("Delete", "todo.delete")
        menu.append_section(None, danger)
        return menu

    def _actions(self, todo):
        win = self._win
        group = Gio.SimpleActionGroup()

        def add(name, callback, param=None):
            action = Gio.SimpleAction.new(name, param)
            action.connect("activate", lambda _a, value: _later(callback, value))
            group.add_action(action)
        add("edit", lambda _v: win.open_editor(todo))
        add("snooze", lambda _v: win.snooze(todo))
        add("toggle", lambda _v: win.set_done(todo, not todo.done))
        add("delete", lambda _v: win.delete(todo))
        add("open-file", lambda v: win.open_attachment(todo, v.get_int32()), GLib.VariantType.new("i"))
        return group


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=480, default_height=720,
                         hide_on_close=True)  # closing keeps the app running in the tray
        self.app = app
        self.store = app.store
        self._editor = None
        self._completed_expanded = False

        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=APP_NAME))
        new_button = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New todo (Ctrl+N)",
                                action_name="app.new")
        header.pack_start(new_button)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=app.build_menu(),
                                       tooltip_text="Main menu", primary=True))

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_top=12, margin_bottom=24, margin_start=12, margin_end=12)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=6)
        self.hero_day = Gtk.Label(xalign=0)
        self.hero_day.add_css_class("hero-day")
        self.hero_summary = Gtk.Label(xalign=0, wrap=True)
        self.hero_summary.add_css_class("dim-label")
        hero.append(self.hero_day)
        hero.append(self.hero_summary)
        content.append(hero)

        quick = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        quick.add_css_class("boxed-list")
        self.entry = Adw.EntryRow(title="Add a todo, press Enter")
        self.entry.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        self.entry.connect("entry-activated", self._on_quick_add)
        details = Gtk.Button(icon_name="alarm-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text="Add with a deadline and reminder…")
        details.add_css_class("flat")
        details.connect("clicked", lambda _b: self.open_editor(title=self.entry.get_text().strip()))
        self.entry.add_suffix(details)
        quick.append(self.entry)
        content.append(quick)

        self.empty = Adw.StatusPage(icon_name="remtodo", title="All caught up",
                                    description="Nothing on your list. Add a todo above, or plan one "
                                                "with a deadline and a reminder.")
        self.empty.add_css_class("compact")
        plan = Gtk.Button(label="Plan a todo", halign=Gtk.Align.CENTER, action_name="app.new")
        plan.add_css_class("pill")
        plan.add_css_class("suggested-action")
        self.empty.set_child(plan)
        content.append(self.empty)

        self.sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.append(self.sections)

        clamp = Adw.Clamp(maximum_size=640, child=content)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=clamp)

        toolbar = Adw.ToolbarView(content=scroller)
        toolbar.add_top_bar(header)
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self.toasts)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.refresh()

    # -- building ---------------------------------------------------------

    def _listbox(self, rows):
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        listbox.connect("row-activated", lambda _lb, row: self.open_editor(row.todo))
        for row in rows:
            listbox.append(row)
        return listbox

    def _section(self, title, css, rows):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        heading = Gtk.Box(spacing=8, margin_start=6)
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("section-title")
        if css:
            label.add_css_class(css)
        count = Gtk.Label(label=str(len(rows)))
        count.add_css_class("section-count")
        count.add_css_class("dim-label")
        heading.append(label)
        heading.append(count)
        box.append(heading)
        box.append(self._listbox(rows))
        return box

    def _completed_section(self, rows):
        expander = Adw.ExpanderRow(title="Completed", subtitle=f"{len(rows)} done",
                                   expanded=self._completed_expanded)
        expander.add_prefix(Gtk.Image.new_from_icon_name("object-select-symbolic"))
        expander.connect("notify::expanded", lambda r, _p: setattr(self, "_completed_expanded", r.get_expanded()))
        clear = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER, tooltip_text="Remove all completed todos")
        clear.add_css_class("flat")
        clear.connect("clicked", lambda _b: _later(self.clear_completed))
        expander.add_suffix(clear)
        for row in rows:
            expander.add_row(row)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        listbox.append(expander)
        return listbox

    def refresh(self):
        now = time.time()
        pending = self.store.pending()
        completed = self.store.completed()

        while (child := self.sections.get_first_child()) is not None:
            self.sections.remove(child)
        grouped = {}
        for todo in pending:
            grouped.setdefault(_section_of(todo, now), []).append(todo)
        for key, title, css in SECTIONS:
            if key in grouped:
                self.sections.append(self._section(title, css, [TodoRow(self, t, now) for t in grouped[key]]))
        if completed:
            self.sections.append(self._completed_section([TodoRow(self, t, now) for t in completed]))
        self.empty.set_visible(not pending)

        today = datetime.fromtimestamp(now)
        self.hero_day.set_label(today.strftime("%A"))
        overdue = len(grouped.get("overdue", []))
        due_today = len(grouped.get("today", []))
        parts = [today.strftime("%-d %B")]
        if overdue:
            parts.append(f"{overdue} overdue")
        if due_today:
            parts.append(f"{due_today} due today")
        if pending:
            parts.append(f"{len(pending)} to do")
        else:
            parts.append("nothing to do")
        self.hero_summary.set_label(" · ".join(parts))

    def toast(self, title, undo=None):
        toast = Adw.Toast(title=GLib.markup_escape_text(title), timeout=UNDO_SECONDS if undo else 3)
        if undo:
            toast.set_button_label("Undo")
            toast.connect("button-clicked", lambda _t: undo())
        self.toasts.add_toast(toast)

    # -- actions ----------------------------------------------------------

    def toggle(self):
        if self.get_visible() and self.is_active():
            self.set_visible(False)
        else:
            self.show_and_focus()

    def show_and_focus(self):
        if self.get_visible() and not self.is_active():
            # Without an activation token Wayland won't raise a mapped window; re-mapping does.
            self.set_visible(False)
        self.refresh()
        self.present()
        if self._editor is None:
            self.entry.grab_focus()

    def open_editor(self, todo=None, title=""):
        if self._editor is not None and not self._editor.closing:
            return
        self._editor = EditorDialog(self.store, todo, title, on_save=self._on_editor_save)
        self._editor.connect("closed", self._on_editor_closed)
        self._editor.present(self)

    def _on_editor_closed(self, dialog):
        if self._editor is dialog:
            self._editor = None

    def _import_files(self, todo_id, paths):
        imported = []
        for path in paths:
            try:
                imported.append(self.store.import_attachment(todo_id, path))
            except OSError as e:
                self.toast(f"Couldn't attach {os.path.basename(path)}: {e.strerror or e}")
        return imported

    def _on_editor_save(self, dialog):
        values = dialog.values()
        todo = dialog.todo
        if todo is None or self.store.get(todo.id) is None:
            todo = Todo(**values)
            todo.attachments = self._import_files(todo.id, dialog.new_files)
            todo.schedule()
            self.entry.set_text("")
            self.store.add(todo)
            if todo.remind_at is not None:
                self.toast(f"Reminder set for {timefmt.when(todo.remind_at)}")
        else:
            timing_changed = (todo.deadline, todo.remind_before) != (values["deadline"], values["remind_before"])
            for key, value in values.items():
                setattr(todo, key, value)
            removed = [a for a in todo.attachments if a not in dialog.kept_attachments]
            self.store.delete_attachment_files(removed)
            todo.attachments = dialog.kept_attachments + self._import_files(todo.id, dialog.new_files)
            if timing_changed:
                todo.schedule()
            self.store.changed()

    def _on_quick_add(self, entry):
        title = entry.get_text().strip()
        if title:
            entry.set_text("")
            self.store.add(Todo(title=title))

    def set_done(self, todo, done):
        if todo.done != done and self.store.get(todo.id) is not None:
            todo.set_done(done)
            self.store.changed()

    def snooze(self, todo):
        if todo.snooze():
            self.store.changed()
            self.toast(f"Snoozed until {timefmt.short_when(todo.remind_at)}")

    def delete(self, todo):
        removed, index = self.store.remove(todo.id)
        if removed is not None:
            self.toast(f"Deleted “{removed.title}”", undo=lambda: self.store.restore([(removed, index)]))

    def clear_completed(self):
        removed = self.store.clear_completed()
        if removed:
            n = len(removed)
            self.toast(f"Cleared {n} completed todo{'s' if n > 1 else ''}",
                       undo=lambda: self.store.restore(removed))

    def open_attachment(self, todo, index):
        if 0 <= index < len(todo.attachments):
            path = self.store.attachment_path(todo.attachments[index])
            if os.path.exists(path):
                open_file(path, self)
            else:
                self.toast(f"“{todo.attachments[index]['name']}” is missing")

    # -- events -----------------------------------------------------------

    def _on_key(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape and self._editor is None:
            self.set_visible(False)
            return True
        return False
