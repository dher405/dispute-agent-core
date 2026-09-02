import sys
import time
import subprocess
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [SUPERVISOR] - %(message)s")
logger = logging.getLogger("Supervisor")

WORKERS = [
    {"name": "Reddit Ingestion", "cmd": [sys.executable, "worker.py"]},
    {"name": "Bluesky Ingestion", "cmd": [sys.executable, "worker_bluesky.py"]}
]

def run_all_once():
    logger.info("Running single pass across all platform ingestion daemons...")
    for w in WORKERS:
        cmd = w["cmd"] + ["--once"]
        logger.info(f"Triggering {w['name']} once...")
        try:
            subprocess.run(cmd, check=True)
        except Exception as e:
            logger.error(f"Execution error on {w['name']}: {e}")

def monitor_daemons():
    processes = {}
    for w in WORKERS:
        logger.info(f"Launching long-running worker: {w['name']}")
        processes[w["name"]] = subprocess.Popen(w["cmd"])

    while True:
        try:
            for name, proc in list(processes.items()):
                if proc.poll() is not None:
                    logger.warning(f"Worker {name} exited with code {proc.returncode}. Restarting...")
                    w_config = next(item for item in WORKERS if item["name"] == name)
                    processes[name] = subprocess.Popen(w_config["cmd"])
            time.sleep(10)
        except KeyboardInterrupt:
            logger.info("Terminating all supervised child processes...")
            for proc in processes.values():
                proc.terminate()
            break

if __name__ == "__main__":
    if "--once" in sys.argv:
        run_all_once()
    else:
        monitor_daemons()
