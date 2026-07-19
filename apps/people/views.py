from django.db.models import Q
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.churches.permissions import led_ministries

from .models import Person
from .permissions import PersonPermission
from .serializers import MeSerializer, PersonSerializer


class PersonViewSet(viewsets.ModelViewSet):
    serializer_class = PersonSerializer
    permission_classes = [PersonPermission]

    def get_queryset(self):
        user = self.request.user
        if user.is_manager:
            return Person.objects.select_related("campus").prefetch_related("ministries").all()
        led = led_ministries(user)
        if led.exists():
            return (
                Person.objects.select_related("campus")
                .prefetch_related("ministries")
                .filter(Q(pk=user.pk) | Q(ministries__in=led))
                .distinct()
            )
        return Person.objects.select_related("campus").prefetch_related("ministries").filter(pk=user.pk)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)
