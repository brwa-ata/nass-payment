"""The package's logger, and the optional file it writes to.

Everything is logged to the ``nass_payment`` logger. A project either routes
that logger in ``LOGGING`` like any other, or sets ``NASS_LOG_FILE`` and the
package writes the file itself -- wherever the project keeps its logs::

    NASS_LOG_FILE = BASE_DIR / 'logs' / 'nass_payment.log'   # a logs/ folder
    NASS_LOG_FILE = 'nass_payment.log'                        # the project root

With neither, nothing is written: Python drops ``INFO`` and prints warnings
and errors to stderr, as it does for any logger nobody configured.
"""

import logging
from pathlib import Path

from django.conf import settings

LOGGER_NAME = 'nass_payment'
FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'

logger = logging.getLogger(LOGGER_NAME)


def log_path(log_file):
    """``NASS_LOG_FILE`` as an absolute path; a relative one is from BASE_DIR."""
    path = Path(log_file)
    if not path.is_absolute():
        path = Path(getattr(settings, 'BASE_DIR', None) or Path.cwd()) / path
    return path.resolve()


def configure():
    """Send the logger to ``NASS_LOG_FILE`` when set. Safe to call twice.

    Called from ``AppConfig.ready()``, after Django has applied ``LOGGING``.
    Creates the file's folder if it is missing. Returns the handler, or None
    when no file is set.
    """
    from .conf import get_conf

    conf = get_conf()
    if not conf.log_file:
        return None

    path = log_path(conf.log_file)
    for handler in logger.handlers:
        if getattr(handler, 'nass_payment_file', None) == path:
            return handler

    path.parent.mkdir(parents=True, exist_ok=True)
    # delay: the file is only created once something is logged
    handler = logging.FileHandler(path, encoding='utf-8', delay=True)
    handler.nass_payment_file = path
    handler.setFormatter(logging.Formatter(FORMAT))
    logger.addHandler(handler)
    logger.setLevel(conf.log_level)
    # the file is where these go; don't also hand them to the root logger
    logger.propagate = False
    return handler
