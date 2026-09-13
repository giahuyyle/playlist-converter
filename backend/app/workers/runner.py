"""Durable outbox relay + RabbitMQ consumer. Run each in its own process."""

import argparse
import json
import logging
import time
from contextlib import contextmanager

from sqlalchemy import select, text

from app.core.config import config
from app.core.database import Session, engine
from app.models.entities import Conversion, Job
from app.providers.base import ProviderError
from app.services.conversions import analyze, transfer

log = logging.getLogger(__name__)


@contextmanager
def conversion_lock(conversion_id):
    # Session-level advisory lock survives checkpoint commits and is released on process death.
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:id, 0))"),
                {"id": conversion_id},
            )
        else:
            acquired = True  # SQLite local mode supports a single worker only.
        try:
            yield acquired
        finally:
            if acquired and engine.dialect.name == "postgresql":
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:id, 0))"),
                    {"id": conversion_id},
                )


def process(job_id):
    with Session() as db:
        job = db.get(Job, job_id)
        if not job or job.status in ("done", "dead") or job.available_at > time.time():
            return
        with conversion_lock(job.conversion_id) as acquired:
            if not acquired:
                return  # Relay republishes uncompleted jobs after visibility timeout.
            db.refresh(job)
            if job.status in ("done", "dead"):
                return
            c = db.get(Conversion, job.conversion_id)
            if c.status in ("review_required", "completed", "failed"):
                job.status = "done"
                db.commit()
                return
            job.attempts += 1
            db.commit()
            try:
                if c.phase == "matching":
                    analyze(db, c)
                else:
                    transfer(db, c)
                job.status = "done"
            except ProviderError as error:
                c.error = str(error)
                if error.retryable and not c.pending_write and job.attempts < 5:
                    job.status = "pending"
                    job.available_at = time.time() + max(
                        error.retry_after, min(300, 5 * 2**job.attempts)
                    )
                    c.status = "queued"
                else:
                    c.status, job.status = "failed", "dead"
            except Exception:
                # Never expose provider bodies, credentials, or exception strings to clients/logs.
                db.rollback()
                c = db.get(Conversion, job.conversion_id)
                job = db.get(Job, job_id)
                c.error = "Unexpected worker failure; review recovery state before retrying"
                c.status, job.status = "failed", "dead"
                log.error("Worker failure for conversion %s", c.id)
            db.commit()


def connection():
    import pika

    params = pika.URLParameters(config.rabbitmq_url)
    params.heartbeat = (
        0  # Synchronous worker may process long playlists; TCP keepalive detects disconnects.
    )
    params.blocked_connection_timeout = 30
    conn = pika.BlockingConnection(params)
    channel = conn.channel()
    channel.exchange_declare(exchange="conversion.dead", exchange_type="fanout", durable=True)
    channel.queue_declare(queue="conversion.dead", durable=True)
    channel.queue_bind(queue="conversion.dead", exchange="conversion.dead")
    channel.queue_declare(
        queue="conversion.requested",
        durable=True,
        arguments={"x-dead-letter-exchange": "conversion.dead"},
    )
    return conn, channel


def relay_once(channel):
    import pika

    with Session() as db:
        jobs = db.scalars(
            select(Job)
            .where(
                Job.status.in_(["pending", "published"]),
                Job.available_at <= time.time(),
            )
            .with_for_update(skip_locked=True)
        )
        for job in jobs:
            if (
                job.status == "published"
                and job.published_at
                and job.published_at > time.time() - 600
            ):
                continue
            channel.basic_publish(
                exchange="",
                routing_key="conversion.requested",
                body=json.dumps({"job_id": job.id, "conversion_id": job.conversion_id}),
                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                mandatory=True,
            )
            job.status, job.published_at = "published", time.time()
        db.commit()


def run(mode):
    if mode == "local":
        if config.dev_env != "dev":
            raise RuntimeError("Local worker is for development only")
        while True:
            with Session() as db:
                ids = list(
                    db.scalars(
                        select(Job.id).where(
                            Job.status.in_(["pending", "published"]),
                            Job.available_at <= time.time(),
                        )
                    )
                )
            for job_id in ids:
                process(job_id)
            time.sleep(1)
    while True:
        conn = None
        try:
            conn, channel = connection()
            if mode == "relay":
                channel.confirm_delivery()
                while True:
                    relay_once(channel)
                    conn.sleep(1)
            else:
                channel.basic_qos(prefetch_count=1)

                def consume(ch, method, properties, body):
                    try:
                        payload = json.loads(body)
                        if not isinstance(payload.get("job_id"), str):
                            raise ValueError()
                    except (ValueError, TypeError, AttributeError):
                        ch.basic_nack(method.delivery_tag, requeue=False)
                        return
                    process(payload["job_id"])
                    with Session() as db:
                        job = db.get(Job, payload["job_id"])
                        dead = job is not None and job.status == "dead"
                    if dead:
                        ch.basic_nack(method.delivery_tag, requeue=False)
                    else:
                        ch.basic_ack(method.delivery_tag)

                channel.basic_consume(queue="conversion.requested", on_message_callback=consume)
                channel.start_consuming()
        except KeyboardInterrupt:
            return
        except Exception:
            log.warning("Queue connection interrupted; reconnecting")
            time.sleep(5)
        finally:
            if conn and conn.is_open:
                conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["relay", "worker", "local"])
    run(parser.parse_args().mode)
