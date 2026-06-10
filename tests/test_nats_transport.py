"""NATS JetStream transport tests — Phase 9.3.4.

These tests require a live NATS server with JetStream enabled.
Skip the entire module unless the USE_NATS environment variable is set.

Run with:
    USE_NATS=1 pytest tests/test_nats_transport.py -v
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("USE_NATS"),
    reason="NATS integration tests require USE_NATS=1 and a running NATS server",
)

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")


@pytest.fixture
async def js():
    """Return a connected JetStream context, cleaned up after the test."""
    import nats

    nc = await nats.connect(NATS_URL)
    js_ctx = nc.jetstream()
    yield js_ctx
    await nc.drain()
    await nc.close()


# ── JetStream round-trip ──────────────────────────────────────────────────────

async def test_jetstream_publish_and_pull(js):
    """Publish a message and pull it from a durable consumer."""
    stream_name = f"KIO_TEST_{uuid.uuid4().hex[:8].upper()}"
    subject = f"kio.test.{uuid.uuid4().hex[:8]}"

    try:
        await js.add_stream(name=stream_name, subjects=[subject])

        payload = b'{"status": "ok", "session_id": "test-session"}'
        await js.publish(subject, payload)

        consumer = await js.pull_subscribe(subject, durable=f"test-consumer-{uuid.uuid4().hex[:6]}")
        messages = await consumer.fetch(1, timeout=3)

        assert len(messages) == 1
        assert messages[0].data == payload
        await messages[0].ack()

    finally:
        try:
            await js.delete_stream(stream_name)
        except Exception:
            pass


async def test_jetstream_publish_metadata_preserved(js):
    """Verify that published message headers are preserved on the consumer side."""
    stream_name = f"KIO_HDRS_{uuid.uuid4().hex[:8].upper()}"
    subject = f"kio.headers.{uuid.uuid4().hex[:8]}"

    try:
        await js.add_stream(name=stream_name, subjects=[subject])

        headers = {"X-Session-Id": "abc123", "X-KIO-ID": "kio2"}
        await js.publish(subject, b"{}", headers=headers)

        consumer = await js.pull_subscribe(subject, durable=f"hdr-consumer-{uuid.uuid4().hex[:6]}")
        messages = await consumer.fetch(1, timeout=3)

        assert len(messages) == 1
        msg = messages[0]
        assert msg.headers.get("X-Session-Id") == "abc123"
        assert msg.headers.get("X-KIO-ID") == "kio2"
        await msg.ack()

    finally:
        try:
            await js.delete_stream(stream_name)
        except Exception:
            pass


# ── Capability announcement ───────────────────────────────────────────────────

async def test_capability_announcement_received(js):
    """A capability announcement published to the KIO subject is consumable."""
    stream_name = f"KIO_CAP_{uuid.uuid4().hex[:8].upper()}"
    subject = f"kio.capability.{uuid.uuid4().hex[:8]}"

    try:
        await js.add_stream(name=stream_name, subjects=[subject])

        announcement = (
            b'{"kio_id": "kio3", "name": "Repo Analyzer", "version": "1.0.0", '
            b'"available": true}'
        )
        await js.publish(subject, announcement)

        consumer = await js.pull_subscribe(subject, durable=f"cap-consumer-{uuid.uuid4().hex[:6]}")
        messages = await consumer.fetch(1, timeout=3)

        assert len(messages) == 1
        import json
        body = json.loads(messages[0].data)
        assert body["kio_id"] == "kio3"
        assert body["available"] is True
        await messages[0].ack()

    finally:
        try:
            await js.delete_stream(stream_name)
        except Exception:
            pass


# ── Heartbeat ─────────────────────────────────────────────────────────────────

async def test_heartbeat_received(js):
    """A heartbeat message published on the heartbeat subject can be pulled."""
    stream_name = f"KIO_HB_{uuid.uuid4().hex[:8].upper()}"
    subject = f"kio.heartbeat.{uuid.uuid4().hex[:8]}"

    try:
        await js.add_stream(name=stream_name, subjects=[subject])

        heartbeat = b'{"kio_id": "kio3", "status": "HEALTHY", "uptime_s": 42}'
        await js.publish(subject, heartbeat)

        consumer = await js.pull_subscribe(subject, durable=f"hb-consumer-{uuid.uuid4().hex[:6]}")
        messages = await consumer.fetch(1, timeout=3)

        assert len(messages) == 1
        import json
        body = json.loads(messages[0].data)
        assert body["status"] == "HEALTHY"
        await messages[0].ack()

    finally:
        try:
            await js.delete_stream(stream_name)
        except Exception:
            pass
