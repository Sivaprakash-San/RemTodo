"""Todo model and JSON persistence."""
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field, fields


def data_dir():
    override = os.environ.get("REMTODO_DATA_DIR")
    if override:
        return override
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "remtodo")


@dataclass
class Todo:
    title: str
    notes: str = ""
    deadline: float | None = None     # epoch seconds
    remind_before: int | None = None  # minutes before the deadline; None = no reminder
    snooze_minutes: int | None = 10   # None = snoozing disabled
    remind_at: float | None = None    # when the next reminder fires
    notified: bool = False            # the reminder for remind_at has been shown
    snoozed: bool = False             # remind_at was set by a snooze
    done: bool = False
    done_at: float | None = None
    attachments: list = field(default_factory=list)  # [{"name", "file" (relative to the attachments dir), "size"}]
    created: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def schedule(self):
        """Recompute the reminder from the deadline. A reminder time already in the past fires right away."""
        if self.done or self.deadline is None or self.remind_before is None:
            self.remind_at = None
        else:
            self.remind_at = self.deadline - self.remind_before * 60
        self.notified = False
        self.snoozed = False

    def snooze(self, now=None):
        if self.snooze_minutes is None or self.done:
            return False
        now = time.time() if now is None else now
        self.remind_at = now + self.snooze_minutes * 60
        self.notified = False
        self.snoozed = True
        return True

    def set_done(self, done, now=None):
        now = time.time() if now is None else now
        self.done = done
        self.done_at = now if done else None
        self.schedule()
        # Re-opening a todo shouldn't immediately replay a reminder that already passed.
        if self.remind_at is not None and self.remind_at <= now:
            self.notified = True

    def is_overdue(self, now):
        return not self.done and self.deadline is not None and self.deadline <= now

    def reminder_fired(self):
        return not self.done and self.remind_at is not None and self.notified

    @classmethod
    def from_dict(cls, data):
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class Store:
    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "todos.json")
        self.todos: list[Todo] = []
        self.settings = {"sound": True}
        self._listeners = []

    @property
    def attachments_dir(self):
        return os.path.join(os.path.dirname(self.path), "attachments")

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            todos = [Todo.from_dict(d) for d in data.get("todos", [])]
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError) as e:
            backup = f"{self.path}.corrupt-{int(time.time())}"
            print(f"remtodo: could not read {self.path} ({e}); moved it to {backup}", file=sys.stderr)
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            return
        self.todos = todos
        self.settings.update(data.get("settings", {}))
        self._prune_attachments()

    def save(self):
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        payload = {
            "version": 1,
            "settings": self.settings,
            "todos": [asdict(t) for t in self.todos],
        }
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".todos-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise

    def connect(self, callback):
        self._listeners.append(callback)

    def changed(self):
        """Persist and notify listeners. Call after mutating any todo."""
        self.save()
        for callback in list(self._listeners):
            callback()

    def get(self, todo_id):
        return next((t for t in self.todos if t.id == todo_id), None)

    def add(self, todo, index=None):
        if index is None:
            self.todos.append(todo)
        else:
            self.todos.insert(index, todo)
        self.changed()

    def remove(self, todo_id):
        """Remove a todo; returns (todo, index) so it can be restored, or (None, -1)."""
        for i, t in enumerate(self.todos):
            if t.id == todo_id:
                del self.todos[i]
                self.changed()
                return t, i
        return None, -1

    def restore(self, removed):
        """Undo remove()/clear_completed(): re-insert (todo, index) pairs at their old positions."""
        for todo, index in sorted(removed, key=lambda pair: pair[1]):
            self.todos.insert(min(index, len(self.todos)), todo)
        self.changed()

    def clear_completed(self):
        """Remove completed todos; returns [(todo, index)] for restore()."""
        removed = [(t, i) for i, t in enumerate(self.todos) if t.done]
        self.todos = [t for t in self.todos if not t.done]
        if removed:
            self.changed()
        return removed

    # -- attachments --------------------------------------------------------
    # Files are copied in, so a todo keeps working when the original moves.
    # A deleted todo's files are kept until the next start, so deleting can be undone.

    def attachment_path(self, attachment):
        return os.path.join(self.attachments_dir, attachment["file"])

    def import_attachment(self, todo_id, source):
        folder = os.path.join(self.attachments_dir, todo_id)
        os.makedirs(folder, exist_ok=True)
        name = os.path.basename(source.rstrip("/")) or "file"
        stem, ext = os.path.splitext(name)
        dest, n = os.path.join(folder, name), 2
        while os.path.exists(dest):
            dest, n = os.path.join(folder, f"{stem} ({n}){ext}"), n + 1
        shutil.copy2(source, dest)
        return {"name": name, "file": os.path.relpath(dest, self.attachments_dir), "size": os.path.getsize(dest)}

    def delete_attachment_files(self, attachments):
        for attachment in attachments:
            path = self.attachment_path(attachment)
            try:
                os.remove(path)
                os.rmdir(os.path.dirname(path))  # only succeeds once the folder is empty
            except OSError:
                pass

    def _prune_attachments(self):
        """Drop attachment folders of todos that no longer exist."""
        try:
            folders = os.listdir(self.attachments_dir)
        except OSError:
            return
        ids = {t.id for t in self.todos}
        for folder in folders:
            if folder not in ids:
                shutil.rmtree(os.path.join(self.attachments_dir, folder), ignore_errors=True)

    def pending(self):
        """Open todos: soonest deadline first (overdue at the top), then those without a deadline."""
        open_todos = [t for t in self.todos if not t.done]
        return sorted(open_todos, key=lambda t: (t.deadline is None, t.deadline or 0, t.created))

    def completed(self):
        return sorted((t for t in self.todos if t.done), key=lambda t: t.done_at or 0, reverse=True)

    def due_reminders(self, now):
        return [
            t for t in self.todos
            if not t.done and not t.notified and t.remind_at is not None and t.remind_at <= now
        ]
