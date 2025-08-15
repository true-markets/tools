import logging

from market_maker.settings import settings

loggers = {}


def cleanup_loggers():
    """Clean up all existing loggers and handlers."""
    for _logger_name, logger in loggers.items():
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
    loggers.clear()


def setup_custom_logger(name, log_level=settings.LOG_LEVEL):
    # Return existing logger if already configured
    if loggers.get(name):
        return loggers[name]

    logger = logging.getLogger(name)
    loggers[name] = logger

    # Only add handler if logger doesn't already have handlers
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(levelname)6s - %(threadName)s/%(module)s:%(lineno)d - %(message)s"
        )
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(log_level)

    # Prevent propagation to avoid duplicate messages
    logger.propagate = False

    return logger
