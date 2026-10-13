"""python3 -m remtodo.install [--uninstall]"""
import sys

from . import desktop


def main(argv):
    if "--uninstall" in argv:
        removed = desktop.uninstall()
        print("Removed:\n  " + "\n  ".join(removed) if removed else "Nothing to remove.")
        return 0
    for path in desktop.install():
        print(f"Installed {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
