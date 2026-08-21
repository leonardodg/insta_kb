import logging

from dotenv import load_dotenv

from core.settings.config import settings
from infra.log.settings.logging import setup_logging

# Load variable keys from the system .env file
load_dotenv()


def main() -> None:
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.debug("Debug logging mode successfully initialized via system variable.")

    logger.info("Hello from app!")
    logger.info(f"App name: {settings.APP_NAME}")


if __name__ == "__main__":
    main()
