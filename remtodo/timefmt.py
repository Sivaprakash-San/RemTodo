"""Human-friendly date and duration formatting."""
import time
from datetime import datetime

use_12h = False


def configure_from_desktop():
    """Follow GNOME's 12h/24h clock setting when it's available."""
    global use_12h
    try:
        from gi.repository import Gio

        source = Gio.SettingsSchemaSource.get_default()
        if source and source.lookup("org.gnome.desktop.interface", True):
            settings = Gio.Settings.new("org.gnome.desktop.interface")
            use_12h = settings.get_string("clock-format") == "12h"
    except Exception:
        pass


def _now(now):
    return time.time() if now is None else now


def clock(ts):
    dt = datetime.fromtimestamp(ts)
    if use_12h:
        return dt.strftime("%I:%M %p").lstrip("0")
    return dt.strftime("%H:%M")


def day(ts, now=None):
    dt = datetime.fromtimestamp(ts)
    today = datetime.fromtimestamp(_now(now)).date()
    days = (dt.date() - today).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    if days == -1:
        return "Yesterday"
    if 1 < days < 7:
        return dt.strftime("%A")
    if dt.year == today.year:
        return dt.strftime("%a %-d %b")
    return dt.strftime("%-d %b %Y")


def when(ts, now=None):
    """'Today 21:30', 'Tomorrow 09:00', 'Saturday 18:00', 'Mon 3 Nov 09:00'."""
    return f"{day(ts, now)} {clock(ts)}"


def short_when(ts, now=None):
    """Just the time when it's today, otherwise day and time."""
    return clock(ts) if day(ts, now) == "Today" else when(ts, now)


def duration(seconds):
    """Compact duration: '5 min', '2 h 15 min', '3 d 4 h'."""
    minutes = round(abs(seconds) / 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours and days < 7 else f"{days} d"


def relative(ts, now=None):
    delta = ts - _now(now)
    if abs(delta) < 60:
        return "due now"
    if delta > 0:
        return f"in {duration(delta)}"
    return f"{duration(delta)} overdue"


def reminder_label(minutes):
    if minutes is None:
        return "No reminder"
    if minutes == 0:
        return "At deadline"
    if minutes % 1440 == 0:
        n = minutes // 1440
        return f"{n} day{'s' if n > 1 else ''} before"
    if minutes % 60 == 0:
        n = minutes // 60
        return f"{n} hour{'s' if n > 1 else ''} before"
    return f"{minutes} min before"
