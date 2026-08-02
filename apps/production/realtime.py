from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def broadcast_event_update(event_id, kind: str) -> None:
    """Avisa quem está com o rundown desse Evento aberto que algo mudou.
    Payload deliberadamente enxuto — só `kind` (ex: "rundown_item_status",
    "demand", "roster_entry", "checklist_item") — o cliente reage invalidando
    a query certa e refetchando via REST."""
    channel_layer = get_channel_layer()
    if channel_layer is None:  # sem CHANNEL_LAYERS configurado (não deveria acontecer)
        return
    async_to_sync(channel_layer.group_send)(
        f"production-event-{event_id}",
        {"type": "event.update", "kind": kind},
    )
