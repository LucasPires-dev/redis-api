import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def broadcast_event_update(event_id, kind: str) -> None:
    """Notifies whoever has this Event's rundown open that something changed.
    Deliberately minimal payload — only `kind` (e.g. "rundown_item_status",
    "demand", "roster_entry", "checklist_item") — the client reacts by
    invalidating the right query and refetching via REST."""
    channel_layer = get_channel_layer()
    if channel_layer is None:  # CHANNEL_LAYERS not configured (shouldn't happen)
        return
    # Best effort: this only refreshes screens that are open. If Redis is down,
    # the change itself is already saved — failing the request would make the
    # client think it wasn't (e.g. "registration failed" for a saved registration).
    try:
        async_to_sync(channel_layer.group_send)(
            f"production-event-{event_id}",
            {"type": "event.update", "kind": kind},
        )
    except Exception:
        logger.warning("Realtime broadcast failed for event %s (%s)", event_id, kind, exc_info=True)
