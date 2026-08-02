import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.utils import timezone

from apps.production.consumers import EventConsumer
from apps.production.models import Event

# transaction=True: o teste padrão (django_db) roda dentro de uma transação aberta
# que nunca comita — a thread que o database_sync_to_async do consumer usa pra
# consultar o banco não enxerga essa transação e trava esperando lock do SQLite.
# Com transaction=True os dados são comitados de verdade (e truncados depois).
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def event(campus, jovens_ministry):
    return Event.objects.create(
        title="Culto", campus=campus, ministry_kind="jovens", starts_at=timezone.now()
    )


async def _connect(person, event_id):
    communicator = WebsocketCommunicator(EventConsumer.as_asgi(), f"/ws/production/events/{event_id}/")
    communicator.scope["user"] = person
    communicator.scope["url_route"] = {"kwargs": {"event_id": str(event_id)}}
    connected, _ = await communicator.connect()
    return communicator, connected


@pytest.mark.asyncio
async def test_authorized_person_connects_and_receives_broadcast(jovens_leader, event):
    communicator, connected = await _connect(jovens_leader, event.id)
    assert connected

    channel_layer = get_channel_layer()
    await channel_layer.group_send(f"production-event-{event.id}", {"type": "event.update", "kind": "demand"})
    message = await communicator.receive_json_from()
    assert message == {"kind": "demand"}

    await communicator.disconnect()


@pytest.mark.asyncio
async def test_technical_ministry_member_can_also_connect(media_member, event):
    """Mesma regra de visibilidade dos endpoints REST (scope_events): técnico do
    campus vê/recebe updates de qualquer evento local, não só o que criou."""
    communicator, connected = await _connect(media_member, event.id)
    assert connected
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_unrelated_person_is_rejected(regular_person, event):
    communicator, connected = await _connect(regular_person, event.id)
    assert not connected


@pytest.mark.asyncio
async def test_anonymous_connection_is_rejected(event):
    from django.contrib.auth.models import AnonymousUser

    communicator, connected = await _connect(AnonymousUser(), event.id)
    assert not connected
