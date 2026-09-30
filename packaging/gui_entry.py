"""Entrada do UniversalMediaTools.exe (interface grafica, sem console)."""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "--engine-worker":
    # so usado se o umd.exe nao estiver ao lado (o app prefere o executavel de console)
    from umd.engine.worker import main as worker_main

    sys.exit(worker_main())

from umd.main import main

sys.exit(main([]))
