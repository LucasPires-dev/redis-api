"""The realtime broadcast only refreshes screens that are open; it must never
fail the request that already saved the change."""

from apps.production import realtime


class BrokenLayer:
    async def group_send(self, group, message):
        raise ConnectionError("Redis is down")


def test_broadcast_survives_a_channel_layer_outage(monkeypatch, caplog):
    monkeypatch.setattr(realtime, "get_channel_layer", lambda: BrokenLayer())

    realtime.broadcast_event_update("some-event", "registration")  # must not raise

    assert "Realtime broadcast failed" in caplog.text
