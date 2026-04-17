from django.urls import path
from .views import (
    ChangePasswordView,
    CsrfView,
    CurrentUserView,
    ForgotPasswordConfirmView,
    ForgotPasswordRequestView,
    LoginView,
    LogoutView,
)

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("me/", CurrentUserView.as_view(), name="auth-me"),
    path("csrf/", CsrfView.as_view(), name="auth-csrf"),
    path("change-password/", ChangePasswordView.as_view(), name="auth-change-password"),
    path("forgot-password/", ForgotPasswordRequestView.as_view(), name="auth-forgot-password"),
    path("reset-password/", ForgotPasswordConfirmView.as_view(), name="auth-reset-password"),
]