from django.urls import path
from .views import LoginView, LogoutView, CurrentUserView, CsrfView

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("me/", CurrentUserView.as_view(), name="auth-me"),
    path("csrf/", CsrfView.as_view(), name="auth-csrf"),
]