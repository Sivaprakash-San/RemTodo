"""Dialog for creating and editing a todo: details, deadline, reminder, snooze and attachments."""
import os
import time
from datetime import datetime, timedelta

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import timefmt  # noqa: E402

REMINDER_CHOICES = [None, 0, 5, 10, 15, 30, 60, 120, 1440]
DEFAULT_REMINDER = 0
DEFAULT_SNOOZE = 10


def _round_up_5(dt):
    dt = dt.replace(second=0, microsecond=0)
    return dt + timedelta(minutes=(5 - dt.minute % 5) % 5)


def _next_at(hour, days=0):
    now = datetime.now()
    target = (now + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _in_one_hour():
    return _round_up_5(datetime.now() + timedelta(hours=1))


PRESETS = [
    ("In 1 hour", "hourglass-symbolic", _in_one_hour),
    ("This evening", "weather-clear-night-symbolic", lambda: _next_at(18)),
    ("Tomorrow morning", "weather-clear-symbolic", lambda: _next_at(9, days=1)),
    ("Next week", "x-office-calendar-symbolic", lambda: _round_up_5(datetime.now() + timedelta(days=7))),
]


def _two_digits(spin):
    spin.set_text(f"{int(spin.get_value()):02d}")
    return True


def file_icon(path):
    """A themed icon for a file, based on its content type."""
    content_type, _uncertain = Gio.content_type_guess(path, None)
    return Gio.content_type_get_symbolic_icon(content_type) if content_type else Gio.ThemedIcon.new("text-x-generic-symbolic")


def open_file(path, parent, folder=False):
    launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
    if folder:
        launcher.open_containing_folder(parent, None, None)
    else:
        launcher.launch(parent, None, None)


class EditorDialog(Adw.Dialog):
    """Calls on_save(dialog) when the user saves; read the result with values()."""

    def __init__(self, store, todo=None, title="", on_save=None):
        super().__init__(title="Edit Todo" if todo else "New Todo", content_width=480, content_height=760)
        self.store = store
        self.todo = todo
        self.on_save = on_save
        self.kept_attachments = list(todo.attachments) if todo else []
        self.new_files = []  # local paths to copy in on save
        self._attachment_rows = []
        self._loading = True
        self.closing = False  # True from Cancel/Save until the close animation ends

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.finish())
        header.pack_start(cancel)
        self.save_button = Gtk.Button(label="Save" if todo else "Add")
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda _b: self._save())
        header.pack_end(self.save_button)
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        self.set_child(toolbar)
        self.set_default_widget(self.save_button)

        page.add(self._build_details())
        page.add(self._build_deadline())
        page.add(self._build_reminder())
        page.add(self._build_attachments())

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        self._load(todo, title)
        self._loading = False
        self._sync()
        self.set_focus(self.title_row)

    # -- building ---------------------------------------------------------

    def _build_details(self):
        group = Adw.PreferencesGroup()
        self.title_row = Adw.EntryRow(title="What needs doing?")
        self.title_row.connect("changed", lambda *_: self._sync())
        self.title_row.connect("entry-activated", lambda *_: self._save())
        group.add(self.title_row)

        self.notes = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                  top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        self.notes.add_css_class("notes-view")
        placeholder = Gtk.Label(label="Notes", xalign=0, valign=Gtk.Align.START,
                                margin_start=12, margin_top=12, can_target=False)
        placeholder.add_css_class("dim-label")
        self.notes.get_buffer().connect(
            "changed", lambda buf: placeholder.set_visible(buf.get_char_count() == 0))
        overlay = Gtk.Overlay(margin_top=12)
        overlay.add_css_class("card")
        overlay.set_child(self.notes)
        overlay.add_overlay(placeholder)
        self.notes.set_size_request(-1, 84)
        group.add(overlay)
        return group

    def _build_deadline(self):
        group = Adw.PreferencesGroup(title="Deadline")
        self.deadline_row = Adw.SwitchRow(title="Set a deadline")
        self.deadline_row.add_prefix(Gtk.Image.new_from_icon_name("alarm-symbolic"))
        self.deadline_row.connect("notify::active", lambda *_: self._sync())
        group.add(self.deadline_row)

        # Date: a calendar in a popover.
        self.calendar = Gtk.Calendar(margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self.calendar.connect("day-selected", lambda *_: self._sync())
        self.date_button = Gtk.MenuButton(valign=Gtk.Align.CENTER, popover=Gtk.Popover(child=self.calendar))
        self.date_button.add_css_class("flat")
        self.date_row = Adw.ActionRow(title="Date", activatable_widget=self.date_button)
        self.date_row.add_prefix(Gtk.Image.new_from_icon_name("x-office-calendar-symbolic"))
        self.date_row.add_suffix(self.date_button)
        group.add(self.date_row)

        # Time: hour/minute spinners (plus AM/PM on a 12-hour clock) in a popover.
        self.twelve_hour = timefmt.use_12h
        self.hour = Gtk.SpinButton.new_with_range(1, 12, 1) if self.twelve_hour else Gtk.SpinButton.new_with_range(0, 23, 1)
        self.minute = Gtk.SpinButton.new_with_range(0, 59, 1)
        self.minute.set_increments(5, 15)
        for spin in (self.hour, self.minute):
            spin.set_wrap(True)
            spin.set_orientation(Gtk.Orientation.VERTICAL)
            spin.set_width_chars(2)
            if spin is self.minute or not self.twelve_hour:
                spin.connect("output", _two_digits)
            spin.connect("value-changed", lambda *_: self._sync())
        self.ampm = Gtk.DropDown.new_from_strings(["AM", "PM"])
        self.ampm.set_valign(Gtk.Align.CENTER)
        self.ampm.connect("notify::selected", lambda *_: self._sync())
        clock = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        colon = Gtk.Label(label=":")
        colon.add_css_class("title-2")
        for widget in (self.hour, colon, self.minute):
            clock.append(widget)
        if self.twelve_hour:
            clock.append(self.ampm)
        self.time_button = Gtk.MenuButton(valign=Gtk.Align.CENTER, popover=Gtk.Popover(child=clock))
        self.time_button.add_css_class("flat")
        self.time_row = Adw.ActionRow(title="Time", activatable_widget=self.time_button)
        self.time_row.add_prefix(Gtk.Image.new_from_icon_name("preferences-system-time-symbolic"))
        self.time_row.add_suffix(self.time_button)
        group.add(self.time_row)

        self.presets = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6,
                                   max_children_per_line=4, margin_top=12, homogeneous=False)
        for text, icon, make in PRESETS:
            chip = Gtk.Button(child=Adw.ButtonContent(label=text, icon_name=icon))
            chip.add_css_class("chip-button")
            chip.connect("clicked", lambda _b, make=make: self._set_deadline(make().timestamp()))
            self.presets.append(chip)
        group.add(self.presets)
        return group

    def _build_reminder(self):
        self.reminder_group = Adw.PreferencesGroup(title="Reminder")
        self.reminder_choices = list(REMINDER_CHOICES)
        if self.todo and self.todo.remind_before not in self.reminder_choices:
            self.reminder_choices.append(self.todo.remind_before)
        labels = Gtk.StringList.new([timefmt.reminder_label(m) for m in self.reminder_choices])
        self.reminder_row = Adw.ComboRow(title="Remind me", model=labels)
        self.reminder_row.add_prefix(Gtk.Image.new_from_icon_name("preferences-system-notifications-symbolic"))
        self.reminder_row.connect("notify::selected", lambda *_: self._sync())
        self.reminder_group.add(self.reminder_row)

        self.snooze_row = Adw.SwitchRow(title="Allow snooze", subtitle="Adds a Snooze button to the reminder")
        self.snooze_row.add_prefix(Gtk.Image.new_from_icon_name("media-playlist-repeat-symbolic"))
        self.snooze_row.connect("notify::active", lambda *_: self._sync())
        self.reminder_group.add(self.snooze_row)

        self.snooze_minutes_row = Adw.SpinRow.new_with_range(1, 1440, 5)
        self.snooze_minutes_row.set_title("Snooze for")
        self.snooze_minutes_row.set_subtitle("minutes")
        self.snooze_minutes_row.add_prefix(Gtk.Image.new_from_icon_name("hourglass-symbolic"))
        self.reminder_group.add(self.snooze_minutes_row)
        return self.reminder_group

    def _build_attachments(self):
        self.files_group = Adw.PreferencesGroup(title="Attachments")
        add = Gtk.Button(child=Adw.ButtonContent(label="Add", icon_name="mail-attachment-symbolic"),
                         valign=Gtk.Align.CENTER, tooltip_text="Attach files")
        add.add_css_class("flat")
        add.connect("clicked", lambda _b: self._choose_files())
        self.files_group.set_header_suffix(add)

        self.files_empty = Adw.ActionRow(title="No files attached", subtitle="Drop files here or click Add",
                                         activatable=True)
        self.files_empty.add_css_class("dim-label")
        self.files_empty.add_prefix(Gtk.Image.new_from_icon_name("folder-documents-symbolic"))
        self.files_empty.connect("activated", lambda _r: self._choose_files())
        self.files_group.add(self.files_empty)
        return self.files_group

    # -- attachments --------------------------------------------------------

    def _rebuild_attachments(self):
        for row in self._attachment_rows:
            self.files_group.remove(row)
        self._attachment_rows = []
        entries = [("kept", a, self.store.attachment_path(a)) for a in self.kept_attachments]
        entries += [("new", p, p) for p in self.new_files]
        for kind, item, path in entries:
            exists = os.path.exists(path)
            name = item["name"] if kind == "kept" else os.path.basename(path)
            if not exists:
                subtitle = "File is missing"
            else:
                subtitle = GLib.format_size(os.path.getsize(path)) + (" · will be attached on save" if kind == "new" else "")
            row = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle=subtitle, activatable=exists)
            row.add_prefix(Gtk.Image.new_from_gicon(file_icon(path)))
            if exists:
                row.connect("activated", lambda _r, path=path: open_file(path, self.get_root()))
                folder = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                                    tooltip_text="Show in folder")
                folder.add_css_class("flat")
                folder.connect("clicked", lambda _b, path=path: open_file(path, self.get_root(), folder=True))
                row.add_suffix(folder)
            remove = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
            remove.add_css_class("flat")
            remove.connect("clicked", lambda _b, kind=kind, item=item: self._remove_attachment(kind, item))
            row.add_suffix(remove)
            self.files_group.add(row)
            self._attachment_rows.append(row)
        self.files_empty.set_visible(not entries)

    def _remove_attachment(self, kind, item):
        (self.kept_attachments if kind == "kept" else self.new_files).remove(item)
        self._rebuild_attachments()

    def _add_files(self, files):
        for gfile in files:
            path = gfile.get_path()
            if path and os.path.isfile(path) and path not in self.new_files:
                self.new_files.append(path)
        self._rebuild_attachments()

    def _choose_files(self):
        dialog = Gtk.FileDialog(title="Attach Files", modal=True)

        def done(dlg, result):
            try:
                files = dlg.open_multiple_finish(result)
            except GLib.Error:
                return  # cancelled
            self._add_files(files[i] for i in range(files.get_n_items()))
        dialog.open_multiple(self.get_root(), None, done)

    def _on_drop(self, _target, value, _x, _y):
        self._add_files(value.get_files())
        return True

    # -- state ------------------------------------------------------------

    def _load(self, todo, title):
        if todo is None:
            self.title_row.set_text(title)
            self._set_deadline(_in_one_hour().timestamp())
            self.deadline_row.set_active(True)
            self.reminder_row.set_selected(self.reminder_choices.index(DEFAULT_REMINDER))
            self.snooze_row.set_active(True)
            self.snooze_minutes_row.set_value(DEFAULT_SNOOZE)
        else:
            self.title_row.set_text(todo.title)
            self.notes.get_buffer().set_text(todo.notes)
            self._set_deadline(todo.deadline or _in_one_hour().timestamp())
            self.deadline_row.set_active(todo.deadline is not None)
            self.reminder_row.set_selected(self.reminder_choices.index(todo.remind_before))
            self.snooze_row.set_active(todo.snooze_minutes is not None)
            self.snooze_minutes_row.set_value(todo.snooze_minutes or DEFAULT_SNOOZE)
        self._rebuild_attachments()

    def _set_deadline(self, ts):
        dt = datetime.fromtimestamp(ts)
        loading, self._loading = self._loading, True  # one _sync at the end, not one per widget
        self.calendar.select_day(GLib.DateTime.new_local(dt.year, dt.month, dt.day, 0, 0, 0))
        if self.twelve_hour:
            self.ampm.set_selected(1 if dt.hour >= 12 else 0)
            self.hour.set_value(dt.hour % 12 or 12)
        else:
            self.hour.set_value(dt.hour)
        self.minute.set_value(dt.minute)
        self._loading = loading
        self._sync()

    def _get_deadline(self):
        self.hour.update()
        self.minute.update()
        date = self.calendar.get_date()
        hour = int(self.hour.get_value())
        if self.twelve_hour:
            hour = hour % 12 + (12 if self.ampm.get_selected() == 1 else 0)
        return datetime(date.get_year(), date.get_month(), date.get_day_of_month(),
                        hour, int(self.minute.get_value())).timestamp()

    def _sync(self):
        """Update labels, hints and which rows are available."""
        if self._loading:
            return
        has_deadline = self.deadline_row.get_active()
        for widget in (self.date_row, self.time_row, self.presets):
            widget.set_visible(has_deadline)

        deadline = self._get_deadline()
        self.date_button.set_label(timefmt.day(deadline))
        self.time_button.set_label(timefmt.clock(deadline))
        if not has_deadline:
            self.deadline_row.set_subtitle("No due date")
        elif deadline <= time.time():
            self.deadline_row.set_subtitle("Already passed: the reminder will go off right away")
        else:
            self.deadline_row.set_subtitle(f"Due {timefmt.relative(deadline)}")

        has_reminder = has_deadline and self._remind_before() is not None
        self.reminder_row.set_sensitive(has_deadline)
        self.reminder_group.set_description(None if has_deadline else "Set a deadline to get a reminder")
        self.snooze_row.set_sensitive(has_reminder)
        self.snooze_minutes_row.set_visible(has_reminder and self.snooze_row.get_active())
        self.save_button.set_sensitive(bool(self.title_row.get_text().strip()))

    def _remind_before(self):
        return self.reminder_choices[self.reminder_row.get_selected()]

    def _save(self):
        if not self.save_button.get_sensitive():
            return
        if self.on_save:
            self.on_save(self)
        self.finish()

    def finish(self):
        self.closing = True
        self.close()

    def values(self):
        buf = self.notes.get_buffer()
        has_deadline = self.deadline_row.get_active()
        self.snooze_minutes_row.update()
        return {
            "title": self.title_row.get_text().strip(),
            "notes": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip(),
            "deadline": self._get_deadline() if has_deadline else None,
            "remind_before": self._remind_before() if has_deadline else None,
            "snooze_minutes": int(self.snooze_minutes_row.get_value()) if self.snooze_row.get_active() else None,
        }
