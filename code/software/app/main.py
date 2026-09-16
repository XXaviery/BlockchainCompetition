from __future__ import annotations
from services.gui_backend import GuiBackend
from ui.main_window import MainWindow

def run(auto_demo: bool = False):
    backend = GuiBackend()
    MainWindow(backend, auto_demo=auto_demo).run()


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
