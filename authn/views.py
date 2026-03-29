from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import Group
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.middleware.csrf import get_token
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated


def get_user_role_names(user):
    return list(user.groups.values_list("name", flat=True))


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
            "roles": get_user_role_names(user),
        })


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            "detail": "CSRF cookie set",
            "csrfToken": get_token(request),
        })