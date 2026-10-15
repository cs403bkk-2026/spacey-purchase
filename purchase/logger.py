"""The service's one logger, as the hub's shared/logger.py:
    from purchase.logger import logger
create_app() calls init_logging(), so importing this module changes nothing."""

import logging

logger = logging.getLogger("purchase")


def init_logging() -> None:
    """Send INFO and above to stderr as "<LEVEL> <message>", once."""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
