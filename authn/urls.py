from django.urls import path
from .views import (
    ChangePasswordView,
    ClinicalProfessionalProfileView,
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
    path("clinical-profile/", ClinicalProfessionalProfileView.as_view(), name="auth-clinical-profile"),
    path("csrf/", CsrfView.as_view(), name="auth-csrf"),
    path("change-password/", ChangePasswordView.as_view(), name="auth-change-password"),
    path("forgot-password/", ForgotPasswordRequestView.as_view(), name="auth-forgot-password"),
    path("reset-password/", ForgotPasswordConfirmView.as_view(), name="auth-reset-password"),
]