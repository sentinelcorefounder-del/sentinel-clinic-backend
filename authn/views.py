from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.middleware.csrf import get_token
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import ensure_csrf_cookie

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import UserSecurityProfile


def get_user_role_names(user):
    return list(user.groups.values_list("name", flat=True))


def get_must_change_password(user):
    profile, _ = UserSecurityProfile.objects.get_or_create(user=user)
    return profile.must_change_password


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")

        user = authenticate(request, username=username, password=password)

        if user is None:
            return Response(
                {"detail": "Invalid credentials"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        login(request, user)

        return Response({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "must_change_password": get_must_change_password(user),
            "roles": get_user_role_names(user),
        })


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response({"detail": "Logged out"})


class CurrentUserView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "must_change_password": get_must_change_password(user),
            "roles": get_user_role_names(user),
        })


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        current_password = request.data.get("current_password", "")
        new_password = request.data.get("new_password", "")

        if not current_password or not new_password:
            return Response(
                {"detail": "Current password and new password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.check_password(current_password):
            return Response(
                {"detail": "Current password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save()

        profile, _ = UserSecurityProfile.objects.get_or_create(user=user)
        profile.must_change_password = False
        profile.save()

        return Response(
            {"detail": "Password updated successfully."},
            status=status.HTTP_200_OK,
        )


class ForgotPasswordRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip()

        if not email:
            return Response(
                {"detail": "Email is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(email__iexact=email, is_active=True).first()

        # Always return success-like response to avoid account enumeration
        if not user:
            return Response(
                {"detail": "If an account exists for that email, a reset link has been sent."},
                status=status.HTTP_200_OK,
            )

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        frontend_base = getattr(settings, "FRONTEND_URL", "").rstrip("/")
        reset_url = f"{frontend_base}/reset-password?uid={uid}&token={token}"

        subject = "Reset your Sentinel Clinic Portal password"
        message = (
            f"Hello {user.first_name or user.username},\n\n"
            f"Use the link below to reset your password:\n\n"
            f"{reset_url}\n\n"
            f"If you did not request this, you can ignore this email."
        )

        send_mail(
            subject,
            message,
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
            [user.email],
            fail_silently=False,
        )

        return Response(
            {"detail": "If an account exists for that email, a reset link has been sent."},
            status=status.HTTP_200_OK,
        )


class ForgotPasswordConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        uid = request.data.get("uid", "")
        token = request.data.get("token", "")
        new_password = request.data.get("new_password", "")

        if not uid or not token or not new_password:
            return Response(
                {"detail": "uid, token, and new_password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id, is_active=True)
        except Exception:
            return Response(
                {"detail": "Invalid reset link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "Reset link is invalid or expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save()

        profile, _ = UserSecurityProfile.objects.get_or_create(user=user)
        profile.must_change_password = False
        profile.save()

        return Response(
            {"detail": "Password has been reset successfully."},
            status=status.HTTP_200_OK,
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            "detail": "CSRF cookie set",
            "csrfToken": get_token(request),
        })