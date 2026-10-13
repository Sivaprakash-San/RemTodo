#!/bin/sh
# Adds RemTodo to the app grid, starts it on login, and links ~/.local/bin/remtodo.
# Run with --uninstall to undo. Your todos (~/.local/share/remtodo) are left untouched.
set -e
HERE="$(dirname "$(readlink -f "$0")")"
PYTHONPATH="$HERE" /usr/bin/python3 -m remtodo.install "$@"
if [ "$1" != "--uninstall" ]; then
    command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    "$HERE/bin/remtodo" --hidden >/dev/null 2>&1 &
    echo "RemTodo is running in the tray."
fi
