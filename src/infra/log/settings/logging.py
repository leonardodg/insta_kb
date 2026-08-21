# logging_config.py
import logging
import logging.config
import sys

from core.settings.config import settings


def get_log_level() -> int:
    """Reads the LOG_LEVEL variable and converts it to a standard Python logging int."""
    # Fallback default value is INFO if variable is missing
    level_str = settings.LOG_LEVEL

    # Validates string against recognized logging names to prevent errors
    valid_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    return valid_levels.get(level_str, logging.INFO)


def setup_logging():
    """Initializes monolith logger utilizing variable configurations."""
    active_level = get_log_level()

    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": active_level,  # Dynamically sets output thresholds
                "formatter": "standard",
                "stream": sys.stdout,
            },
        },
        "loggers": {
            "": {  # Root engine target configuration
                "handlers": ["console"],
                "level": active_level,  # Ensures root intercepts lower priority entries
                "propagate": True,
            },
        },
    }
    logging.config.dictConfig(log_config)
