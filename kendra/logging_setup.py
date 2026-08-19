from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        # stdout is a machine-readable transport for commands such as the
        # native desktop bridge. Human diagnostics must never corrupt it.
        stream=sys.stderr,
    )
