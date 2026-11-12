from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.churches.membership import campus_for_invite
from apps.churches.permissions import led_campuses, led_ministries
from apps.churches.serializers import CampusPublicSerializer
from apps.churches.throttles import CampusInviteThrottle
from apps.reconciliation.models import Reconciliation

from .models import Person
from .permissions import PersonPermission
from .serializers import MeSerializer, PersonSerializer, RegisterSerializer


class PersonViewSet(viewsets.ModelViewSet):
    """People CRUD at /api/people/ (list, retrieve, create, update, delete).

    Access control works in two layers, which must stay in sync:
    - `get_queryset` decides WHICH people the user can see. Anyone outside the
      queryset doesn't show up in the list and returns 404 on detail.
    - `PersonPermission` decides WHAT the user can do (who can register people
      and who can edit each person).
    """

    serializer_class = PersonSerializer
    permission_classes = [PersonPermission]

    def get_queryset(self):
        """Returns the people visible to the logged-in user.

        - Manager: everyone.
        - Ministry leader/co-leader, campus leader/co-leader or reconciliador:
          themselves, members of the ministries they lead, people at the
          campuses they lead and the converts they follow.
        - Everyone else: only their own record.
        """
        user = self.request.user

        # select_related/prefetch_related avoid N+1 queries in the serializer,
        # which returns a nested `campus` and the `ministries` list per person.
        base = Person.objects.select_related("campus").prefetch_related("ministries")

        if user.is_manager:
            return base.all()

        # Lazy querysets: nothing runs yet. They are used below both in
        # .exists() and as subqueries inside the filter.
        led_ministry_qs = led_ministries(user)
        led_campus_qs = led_campuses(user)

        # A reconciliador (even one with no leadership at all — the common case)
        # must reach their own assigned convert's Person record, chiefly to
        # update baptized/baptized_at. is_reconciliador_of in PersonPermission
        # only authorizes; this queryset is what actually surfaces the object
        # (otherwise the response would be 404).
        reconciliation_convert_ids = Reconciliation.objects.filter(reconciliador=user).values_list(
            "convert_id", flat=True
        )

        if led_ministry_qs.exists() or led_campus_qs.exists() or reconciliation_convert_ids.exists():
            return base.filter(
                Q(pk=user.pk)  # themselves
                | Q(ministries__in=led_ministry_qs)  # members of ministries they lead
                | Q(campus__in=led_campus_qs)  # people at campuses they lead
                | Q(pk__in=reconciliation_convert_ids)  # converts they follow
            ).distinct()  # the ministries join can repeat the same person

        # No leadership and no reconciliation: they only see themselves.
        return base.filter(pk=user.pk)


class MeView(APIView):
    """GET /api/auth/me/ — profile of the logged-in user.

    Returns the person's data and what they lead (see MeSerializer). The
    frontend calls this endpoint after login to build the navigation and decide
    which screens to show.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # request.user is already the logged-in Person (Person is the AUTH_USER_MODEL).
        return Response(MeSerializer(request.user).data)


class RegisterView(APIView):
    """POST /api/auth/register/ — anyone creates their own account (as a member,
    never with a role) and gets the same tokens as /api/auth/login/."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = serializer.save()
        refresh = RefreshToken.for_user(person)
        return Response(
            {"refresh": str(refresh), "access": str(refresh.access_token)},
            status=status.HTTP_201_CREATED,
        )


class InviteLookupView(APIView):
    """GET /api/auth/invite/{code}/ — which campus an invite is for, so the sign-up
    screen opened from the link can show it before the account exists."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [CampusInviteThrottle]

    def get(self, request, code):
        campus = campus_for_invite(code)
        if campus is None:
            raise NotFound("Código de convite inválido.")
        return Response(CampusPublicSerializer(campus).data)
