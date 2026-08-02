from django.urls import re_path

from .consumers import EventConsumer

websocket_urlpatterns = [
    re_path(r"^ws/production/events/(?P<event_id>[0-9a-fA-F-]+)/$", EventConsumer.as_asgi()),
]
