import time
import logging
from worker import run_ingestion_cycle
from carrier_retry_worker import run_retry_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [SUPERVISOR] - %(message)s")
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Starting unified background worker daemon...")
    while True:
        try:
            run_retry_cycle()
            run_ingestion_cycle()
        except Exception as e:
            logger.error(f"Supervisor loop error: {e}")
        time.sleep(45)
