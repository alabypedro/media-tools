"""Entrada do umd.exe: linha de comando e worker das engines."""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "--engine-worker":
    from umd.engine.worker import main as worker_main

    sys.exit(worker_main())

from umd.cli import main

sys.exit(main(sys.argv[1:]))
