import os
import tempfile
import unittest
from datetime import datetime

from remtodo import timefmt
from remtodo.store import Store, Todo

NOW = datetime(2026, 10, 10, 12, 0).timestamp()


class TodoTest(unittest.TestCase):
    def test_schedule_from_deadline(self):
        t = Todo("call", deadline=NOW + 3600, remind_before=15)
        t.schedule()
        self.assertEqual(t.remind_at, NOW + 3600 - 900)
        self.assertFalse(t.notified)

    def test_no_reminder_without_deadline(self):
        t = Todo("x", remind_before=15)
        t.schedule()
        self.assertIsNone(t.remind_at)

    def test_snooze(self):
        t = Todo("x", deadline=NOW, remind_before=0, snooze_minutes=10)
        t.schedule()
        t.notified = True
        self.assertTrue(t.snooze(now=NOW))
        self.assertEqual(t.remind_at, NOW + 600)
        self.assertTrue(t.snoozed)
        self.assertFalse(t.notified)

    def test_snooze_disabled(self):
        t = Todo("x", deadline=NOW, remind_before=0, snooze_minutes=None)
        self.assertFalse(t.snooze(now=NOW))

    def test_reopen_does_not_replay_past_reminder(self):
        t = Todo("x", deadline=NOW - 60, remind_before=0)
        t.set_done(True, now=NOW)
        self.assertIsNone(t.remind_at)
        t.set_done(False, now=NOW)
        self.assertTrue(t.notified)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "todos.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_roundtrip_and_order(self):
        s = Store(self.path)
        s.add(Todo("later", deadline=NOW + 7200))
        s.add(Todo("no deadline"))
        s.add(Todo("overdue", deadline=NOW - 60))
        s.settings["sound"] = False
        s.save()
        s2 = Store(self.path)
        s2.load()
        self.assertEqual([t.title for t in s2.pending()], ["overdue", "later", "no deadline"])
        self.assertFalse(s2.settings["sound"])

    def test_due_reminders(self):
        s = Store(self.path)
        due = Todo("due", deadline=NOW + 300, remind_before=10)
        future = Todo("future", deadline=NOW + 3600, remind_before=10)
        for t in (due, future):
            t.schedule()
            s.todos.append(t)
        self.assertEqual(s.due_reminders(NOW), [due])
        due.notified = True
        self.assertEqual(s.due_reminders(NOW), [])

    def test_corrupt_file_is_backed_up(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        s = Store(self.path)
        s.load()
        self.assertEqual(s.todos, [])
        self.assertTrue(any(n.startswith("todos.json.corrupt-") for n in os.listdir(self.dir.name)))

    def test_attachments_copy_and_prune(self):
        s = Store(self.path)
        src = os.path.join(self.dir.name, "notes.txt")
        with open(src, "w") as f:
            f.write("hello")
        keep, gone = Todo("keep"), Todo("gone")
        a1 = s.import_attachment(keep.id, src)
        a2 = s.import_attachment(keep.id, src)
        self.assertEqual(a2["name"], "notes.txt")
        self.assertNotEqual(a1["file"], a2["file"])  # second copy gets a unique file name
        s.import_attachment(gone.id, src)
        keep.attachments = [a1, a2]
        s.todos = [keep]
        s.save()
        s2 = Store(self.path)
        s2.load()
        self.assertEqual(s2.todos[0].attachments, [a1, a2])
        self.assertTrue(os.path.exists(s2.attachment_path(a1)))
        self.assertFalse(os.path.exists(os.path.join(s.attachments_dir, gone.id)))

    def test_clear_completed_and_restore(self):
        s = Store(self.path)
        a, b, c = Todo("a"), Todo("b", done=True), Todo("c")
        s.todos = [a, b, c]
        removed = s.clear_completed()
        self.assertEqual(s.todos, [a, c])
        s.restore(removed)
        self.assertEqual(s.todos, [a, b, c])


class TimefmtTest(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(timefmt.duration(300), "5 min")
        self.assertEqual(timefmt.duration(8100), "2 h 15 min")
        self.assertEqual(timefmt.duration(3 * 86400 + 4 * 3600), "3 d 4 h")

    def test_relative(self):
        self.assertEqual(timefmt.relative(NOW + 600, NOW), "in 10 min")
        self.assertEqual(timefmt.relative(NOW - 3600, NOW), "1 h overdue")
        self.assertEqual(timefmt.relative(NOW + 10, NOW), "due now")

    def test_day(self):
        self.assertEqual(timefmt.day(NOW + 86400, NOW), "Tomorrow")
        self.assertEqual(timefmt.day(NOW, NOW), "Today")

    def test_reminder_label(self):
        self.assertEqual(timefmt.reminder_label(0), "At deadline")
        self.assertEqual(timefmt.reminder_label(60), "1 hour before")
        self.assertEqual(timefmt.reminder_label(1440), "1 day before")
        self.assertEqual(timefmt.reminder_label(15), "15 min before")


if __name__ == "__main__":
    unittest.main()
