# RemTodo

A small Linux tray app for todos with deadlines, reminders and optional snooze.

- **Tray icon**: left-click opens or closes the todo list, middle-click starts a new todo, and right-click shows a menu with your next todos and their deadlines. The icon gets a red dot when something is overdue.
- **Todos**: each one has a title, notes, an optional deadline and attached files. The list is grouped into Overdue, Today, Tomorrow, Upcoming and No deadline. Overdue ones show in red and ones due within the hour in amber. Completed todos move to a collapsible section, and deleting or clearing can be undone from the toast that pops up.
- **Attachments**: add files from the editor with **Add** or by dropping them onto it. Files are copied into RemTodo's data folder, so they stay with the todo even if the original moves. Open them from the editor or from a todo's **⋮** menu.
- **Reminders**: choose when to be reminded, from at the deadline to a day before. Reminders appear as notifications that stay on screen until you act on them, with **Snooze**, **Mark done** and **Open** buttons and an optional alarm sound.
- **Snooze**: set per todo (on by default, 10 min). You can snooze from the notification or from the list. Reminders missed while the app wasn't running fire at the next start. If more than three are due at once, you get a single summary notification instead.

## Requirements

Python 3 with the GTK 4, libadwaita (1.5 or newer) and libnotify bindings, plus a panel that supports StatusNotifierItem tray icons. On Ubuntu all of this is installed by default, and the tray icon is shown by the *Ubuntu AppIndicators* extension. On other GNOME setups, install the *AppIndicator and KStatusNotifierItem Support* extension. KDE, XFCE and Cinnamon support these tray icons out of the box.

```sh
# Debian/Ubuntu, if anything is missing
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-notify-0.7
```

## Run

```sh
bin/remtodo            # start (or show the window if it's already running)
bin/remtodo --hidden   # start in the tray only
bin/remtodo --new      # open the "New todo" dialog (handy as a keyboard shortcut)
bin/remtodo --quit
```

Only one instance runs at a time. Running the command again talks to the instance that is already running.

## Install

```sh
./install.sh             # app-grid entry, start on login, ~/.local/bin/remtodo
./install.sh --uninstall
```

You can also turn **Start on login** on or off from the window's menu.

## Keyboard

- `Enter` in the top field adds a todo without a deadline.
- The alarm button next to that field, `+` or `Ctrl+N` opens the full editor, where `Enter` in the title saves.
- `Esc` hides the window.
- `Ctrl+Q` quits.

## Data

Todos are stored in `~/.local/share/remtodo/todos.json` and written atomically. Attachments live in `~/.local/share/remtodo/attachments/`. A deleted todo's files are kept until the next start, so the delete can still be undone. If the file is ever unreadable, it is moved aside as `todos.json.corrupt-<time>` instead of being overwritten. Set `REMTODO_DATA_DIR` to use a different folder.

## Development

```sh
/usr/bin/python3 -m unittest discover -s tests -t .
```

| File | What it does |
| --- | --- |
| `remtodo/app.py` | Single-instance `Adw.Application` that wires the pieces together |
| `remtodo/tray.py` | Tray icon over StatusNotifierItem and dbusmenu, using Gio only, no AppIndicator library |
| `remtodo/reminders.py` | Checks for due reminders every 10 s; notifications and their actions |
| `remtodo/window.py`, `editor.py` | The todo list and the create/edit dialog (GTK 4 and libadwaita) |
| `remtodo/store.py` | `Todo` model, scheduling and snooze rules, JSON storage |
