from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import Event
from .permissions import scope_events


class EventConsumer(AsyncJsonWebsocketConsumer):
    """Stream somente leitura: o cliente nunca manda mensagem, só recebe um
    aviso enxuto (`kind`) toda vez que algo muda no Evento — quem recebe reage
    invalidando a query REST correspondente e refetchando, em vez de tentar
    sincronizar o shape completo por dois caminhos (WS e REST)."""

    async def connect(self):
        self.event_id = self.scope["url_route"]["kwargs"]["event_id"]
        user = self.scope["user"]
        if not await self._can_view_event(user, self.event_id):
            await self.close(code=4403)
            return
        self.group_name = f"production-event-{self.event_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    @database_sync_to_async
    def _can_view_event(self, user, event_id) -> bool:
        if not user or not user.is_authenticated:
            return False
        # Reaproveita 100% a regra de visibilidade já usada pelos endpoints REST
        # (scope_events) — nenhuma duplicação de permissão pro WebSocket.
        return scope_events(Event.objects.filter(pk=event_id), user).exists()

    async def event_update(self, event):
        await self.send_json({"kind": event["kind"]})
