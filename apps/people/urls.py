from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import InviteLookupView, MeView, PersonViewSet, RegisterView

router = DefaultRouter()
router.register("people", PersonViewSet, basename="person")

urlpatterns = [
    path("auth/login/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/invite/<str:code>/", InviteLookupView.as_view(), name="invite-lookup"),
    *router.urls,
]
