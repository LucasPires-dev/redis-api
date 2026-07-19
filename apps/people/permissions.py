from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.churches.permissions import led_ministries


def is_any_ministry_leader(person) -> bool:
    return led_ministries(person).exists()


class PersonPermission(BasePermission):
    """Manager: full access to every person.
    Ministry leader/co-leader (any ministry): can register people and manage
    (view/edit) people who participate in a ministry they lead, plus themselves.
    Everyone else: can only see/edit their own record."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method == "POST":
            return request.user.is_manager or is_any_ministry_leader(request.user)
        return True

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_manager or obj.pk == user.pk:
            return True
        if request.method not in SAFE_METHODS and not is_any_ministry_leader(user):
            return False
        led = led_ministries(user)
        return obj.ministries.filter(id__in=led.values("id")).exists()
