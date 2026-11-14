from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.production.permissions import can_manage_event


class TicketConfigPermission(BasePermission):
    """Only event managers (producer, campus leader, manager) can CRUD ticket config."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            from apps.production.models import Event

            event_id = request.data.get("event_id")
            event = Event.objects.filter(pk=event_id).first() if event_id else None
            if event is None:
                return True  # serializer will raise 400
            return can_manage_event(request.user, event)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_event(request.user, obj.event)


class TicketTypePermission(BasePermission):
    """Ticket types: anyone who sees the event reads them; its organizers change them."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            from .models import EventTicketConfig

            config_id = request.data.get("config_id")
            config = EventTicketConfig.objects.filter(pk=config_id).select_related("event").first()
            if not config_id or config is None:
                return True  # serializer will raise 400
            return can_manage_event(request.user, config.event)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_event(request.user, obj.config.event)


class RegistrationPermission(BasePermission):
    """Any authenticated user can register (create). Listing: users see their
    own; event managers see all (filtered in the viewset queryset). Cancel: own
    registration only."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if view.action == "cancel":
            return obj.person_id == request.user.pk
        return can_manage_event(request.user, obj.event)
