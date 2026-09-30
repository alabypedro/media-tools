"""python -m umd              -> interface grafica
python -m umd URL [...]    -> linha de comando
python -m umd --help       -> ajuda da linha de comando
"""

from __future__ import annotations

import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--engine-worker":
        from .engine.worker import main as worker_main

        return worker_main()
    if not argv or argv[0] == "--gui":
        from .main import main as gui_main

        return gui_main([])
    from .cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
