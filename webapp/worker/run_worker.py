"""Entry point for the background solve worker.

Run it (with Redis available) as:

    python -m webapp.worker.run_worker

It configures error logging first so any worker-level crash lands in log.txt,
then consumes the "solves" queue.
"""

from __future__ import annotations

from webapp import config
from webapp.logging_setup import setup_logging


def main() -> None:
    setup_logging()
    from redis import Redis
    from rq import Queue, Worker

    connection = Redis.from_url(config.REDIS_URL)
    worker = Worker([Queue("solves", connection=connection)], connection=connection)
    worker.work()


if __name__ == "__main__":
    main()
