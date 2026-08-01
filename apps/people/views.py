from django.db.models import Q
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.churches.permissions import led_campuses, led_ministries
from apps.reconciliation.models import Reconciliation

from .models import Person
from .permissions import PersonPermission
from .serializers import MeSerializer, PersonSerializer


class PersonViewSet(viewsets.ModelViewSet):
    serializer_class = PersonSerializer
    permission_classes = [PersonPermission]

    def get_queryset(self):
        user = self.request.user
        base = Person.objects.select_related("campus").prefetch_related("ministries")
        if user.is_manager:
            return base.all()
        led_ministry_qs = led_ministries(user)
        led_campus_qs = led_campuses(user)
        # A reconciliador (even one with no leadership at all — the common case)
        # must reach their own assigned convert's Person record, chiefly to update
        # baptized/baptized_at — see is_reconciliador_of in PersonPermission, which
        # this queryset must actually surface the object for, not just authorize.
        reconciliation_convert_ids = Reconciliation.objects.filter(reconciliador=user).values_list(
            "convert_id", flat=True
        )
        if led_ministry_qs.exists() or led_campus_qs.exists() or reconciliation_convert_ids.exists():
            return base.filter(
                Q(pk=user.pk)
                | Q(ministries__in=led_ministry_qs)
                | Q(campus__in=led_campus_qs)
                | Q(pk__in=reconciliation_convert_ids)
            ).distinct()
        return base.filter(pk=user.pk)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)
