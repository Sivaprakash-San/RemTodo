"""Tray icon over the StatusNotifierItem D-Bus protocol.

GNOME (with the AppIndicator extension), KDE, XFCE, Cinnamon and most other
Linux panels host StatusNotifierItems, so no AppIndicator library is needed.
Left click calls Activate (we open the window); the right-click menu is
served over com.canonical.dbusmenu.
"""
import os
import sys

from gi.repository import Gio, GLib

SNI_IFACE = "org.kde.StatusNotifierItem"
SNI_PATH = "/StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

INTROSPECTION = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""

V = GLib.Variant


def _later(callback):
    def run():
        callback()
        return False
    GLib.idle_add(run)


class TrayIcon:
    """A StatusNotifierItem with a dbusmenu context menu.

    Menu items are (label, callback) tuples; a callback of None makes the item
    disabled, and None in place of a tuple is a separator.
    """

    def __init__(self, item_id, icon_theme_path, icon_name, attention_icon_name,
                 on_activate, on_secondary_activate=None):
        self._id = item_id
        self._icon_theme_path = icon_theme_path
        self._icon_name = icon_name
        self._attention_icon_name = attention_icon_name
        self._on_activate = on_activate
        self._on_secondary_activate = on_secondary_activate or on_activate
        self._title = item_id
        self._tooltip = ""
        self._status = "Active"
        self._items = []
        self._labels = []
        self._revision = 1

        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        # get_property=None routes Properties.Get/GetAll to _on_call, which keeps this simple.
        self._bus.register_object(SNI_PATH, self._node.lookup_interface(SNI_IFACE), self._on_call, None, None)
        self._bus.register_object(MENU_PATH, self._node.lookup_interface(MENU_IFACE), self._on_call, None, None)
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._watch_id = 0
        Gio.bus_own_name_on_connection(
            self._bus, self._bus_name, Gio.BusNameOwnerFlags.NONE, self._on_name_acquired, None)

    # -- public API -------------------------------------------------------

    def set_status(self, *, title, tooltip, attention):
        if title != self._title:
            self._title = title
            self._emit(SNI_PATH, SNI_IFACE, "NewTitle")
        if tooltip != self._tooltip:
            self._tooltip = tooltip
            self._emit(SNI_PATH, SNI_IFACE, "NewToolTip")
        status = "NeedsAttention" if attention else "Active"
        if status != self._status:
            self._status = status
            self._emit(SNI_PATH, SNI_IFACE, "NewStatus", V("(s)", (status,)))

    def set_menu(self, items):
        self._items = list(items)
        labels = [item[0] if item else None for item in self._items]
        if labels != self._labels:
            self._labels = labels
            self._revision += 1
            self._emit(MENU_PATH, MENU_IFACE, "LayoutUpdated", V("(ui)", (self._revision, 0)))

    # -- registration with the panel -------------------------------------

    def _on_name_acquired(self, _conn, _name):
        if not self._watch_id:
            # Fires now if a panel is running, and again whenever the panel restarts.
            self._watch_id = Gio.bus_watch_name_on_connection(
                self._bus, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE, self._on_watcher_appeared, None)

    def _on_watcher_appeared(self, _conn, _name, _owner):
        self._bus.call(
            WATCHER_NAME, WATCHER_PATH, WATCHER_NAME, "RegisterStatusNotifierItem",
            V("(s)", (self._bus_name,)), None, Gio.DBusCallFlags.NONE, -1, None,
            self._on_registered, None)

    def _on_registered(self, bus, result, _data):
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            print(f"remtodo: tray registration failed: {e.message}", file=sys.stderr)

    def _emit(self, path, iface, signal, params=None):
        try:
            self._bus.emit_signal(None, path, iface, signal, params)
        except GLib.Error:
            pass

    # -- D-Bus dispatch ---------------------------------------------------

    def _on_call(self, _conn, _sender, path, iface, method, params, invocation):
        try:
            args = params.unpack()
            if iface == PROPS_IFACE:
                self._handle_properties(path, method, args, invocation)
                return
            prefix = "_sni_" if path == SNI_PATH else "_menu_"
            handler = getattr(self, prefix + method, None)
            if handler is None:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
                return
            invocation.return_value(handler(*args))
        except Exception as e:  # never let an exception leave a D-Bus call unanswered
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))

    def _handle_properties(self, path, method, args, invocation):
        props = self._sni_properties() if path == SNI_PATH else self._menu_properties()
        if method == "Get":
            value = props.get(args[1])
            if value is None:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownProperty", args[1])
            else:
                invocation.return_value(V("(v)", (value,)))
        elif method == "GetAll":
            invocation.return_value(V("(a{sv})", (props,)))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.PropertyReadOnly", "Properties are read-only")

    # -- StatusNotifierItem ----------------------------------------------

    def _sni_properties(self):
        return {
            "Category": V("s", "ApplicationStatus"),
            "Id": V("s", self._id),
            "Title": V("s", self._title),
            "Status": V("s", self._status),
            "WindowId": V("i", 0),
            "IconThemePath": V("s", self._icon_theme_path),
            "IconName": V("s", self._icon_name),
            "IconPixmap": V("a(iiay)", []),
            "OverlayIconName": V("s", ""),
            "OverlayIconPixmap": V("a(iiay)", []),
            "AttentionIconName": V("s", self._attention_icon_name),
            "AttentionIconPixmap": V("a(iiay)", []),
            "AttentionMovieName": V("s", ""),
            "ToolTip": V("(sa(iiay)ss)", (self._icon_name, [], self._title, self._tooltip)),
            "ItemIsMenu": V("b", False),
            "Menu": V("o", MENU_PATH),
        }

    def _sni_Activate(self, _x, _y):
        _later(self._on_activate)

    def _sni_SecondaryActivate(self, _x, _y):
        _later(self._on_secondary_activate)

    def _sni_ContextMenu(self, _x, _y):
        pass

    def _sni_Scroll(self, _delta, _orientation):
        pass

    # -- dbusmenu ---------------------------------------------------------
    # Item ids are list index + 1; id 0 is the root.

    def _menu_properties(self):
        return {
            "Version": V("u", 3),
            "TextDirection": V("s", "ltr"),
            "Status": V("s", "normal"),
            "IconThemePath": V("as", []),
        }

    def _item_props(self, item_id):
        if item_id == 0:
            return {"children-display": V("s", "submenu")}
        item = self._items[item_id - 1]
        if item is None:
            return {"type": V("s", "separator")}
        label, callback = item
        props = {"label": V("s", label.replace("_", "__"))}  # "_" marks a mnemonic
        if callback is None:
            props["enabled"] = V("b", False)
        return props

    def _valid(self, item_id):
        return 0 <= item_id <= len(self._items)

    def _menu_GetLayout(self, parent_id, _depth, _names):
        if not self._valid(parent_id):
            raise ValueError(f"no menu item {parent_id}")
        children = []
        if parent_id == 0:
            children = [V("(ia{sv}av)", (i, self._item_props(i), []))
                        for i in range(1, len(self._items) + 1)]
        return V("(u(ia{sv}av))", (self._revision, (parent_id, self._item_props(parent_id), children)))

    def _menu_GetGroupProperties(self, ids, _names):
        ids = ids or range(len(self._items) + 1)
        return V("(a(ia{sv}))", ([(i, self._item_props(i)) for i in ids if self._valid(i)],))

    def _menu_GetProperty(self, item_id, name):
        if not self._valid(item_id) or name not in self._item_props(item_id):
            raise ValueError(f"no property {name} on {item_id}")
        return V("(v)", (self._item_props(item_id)[name],))

    def _menu_Event(self, item_id, event_id, _data, _timestamp):
        if event_id == "clicked" and 0 < item_id <= len(self._items):
            item = self._items[item_id - 1]
            if item and item[1]:
                _later(item[1])

    def _menu_EventGroup(self, events):
        for item_id, event_id, data, timestamp in events:
            self._menu_Event(item_id, event_id, data, timestamp)
        return V("(ai)", ([],))

    def _menu_AboutToShow(self, _item_id):
        return V("(b)", (False,))

    def _menu_AboutToShowGroup(self, _ids):
        return V("(aiai)", ([], []))
