from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .service import group_name


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """The signed-in person's own stream. Read-only: it only says "there is a
    new notification" (with its title, for a toast); the client refetches the
    list and the unread count via REST."""

    async def connect(self):
        user = self.scope["user"]
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return
        self.group_name = group_name(user.pk)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def notification_new(self, event):
        await self.send_json({"kind": "notification", "title": event.get("title", "")})
