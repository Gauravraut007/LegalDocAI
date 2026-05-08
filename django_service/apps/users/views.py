"""Auth + profile views."""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from . import serializers as _ser
from . import tokens as _tok
from .throttling import (
    LoginRateThrottle,
    PasswordResetRateThrottle,
    RegisterRateThrottle,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def _client_ip(request) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


# ----------------------------------------------------------------- register --
@extend_schema(tags=["auth"], request=_ser.RegisterSerializer,
               responses={201: _ser.UserPublicSerializer})
class RegisterView(generics.CreateAPIView):
    serializer_class = _ser.RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterRateThrottle]

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        return Response(
            _ser.UserPublicSerializer(user).data,
            status=status.HTTP_201_CREATED,
        )


# -------------------------------------------------------------------- login --
@extend_schema(tags=["auth"])
class LoginView(TokenObtainPairView):
    throttle_classes = [LoginRateThrottle]

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            email = request.data.get("email")
            if email:
                ip = _client_ip(request)
                User.objects.filter(email__iexact=email).update(last_login_ip=ip)
        return response


@extend_schema(tags=["auth"])
class TokenRefreshThrottledView(TokenRefreshView):
    permission_classes = [permissions.AllowAny]


# -------------------------------------------------------------------- logout --
@extend_schema(tags=["auth"], request={"application/json": {"type": "object",
               "properties": {"refresh": {"type": "string"}}, "required": ["refresh"]}},
               responses={205: OpenApiResponse(description="Refresh token blacklisted")})
class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response({"detail": "refresh token required"}, status=400)
        try:
            token = RefreshToken(refresh)
            token.blacklist()
        except TokenError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(status=status.HTTP_205_RESET_CONTENT)


# ----------------------------------------------------------------------- me --
@extend_schema(tags=["auth"])
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = _ser.UserPublicSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        if self.request.method in ("PATCH", "PUT"):
            return _ser.UserUpdateSerializer
        return _ser.UserPublicSerializer

    def update(self, request, *args, **kwargs):
        super().update(request, *args, **kwargs)
        # Always return the public representation regardless of write serializer.
        return Response(_ser.UserPublicSerializer(self.get_object()).data)


# ---------------------------------------------------------- password change --
@extend_schema(tags=["auth"], request=_ser.PasswordChangeSerializer,
               responses={200: OpenApiResponse(description="Password changed")})
class PasswordChangeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ser = _ser.PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response({"detail": "password changed"})


# ----------------------------------------------------------- password reset --
@extend_schema(tags=["auth"], request=_ser.PasswordResetRequestSerializer,
               responses={202: OpenApiResponse(description="Reset email queued")})
class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetRateThrottle]

    def post(self, request):
        ser = _ser.PasswordResetRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = ser.validated_data["email"].strip().lower()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            # Always 202 — do not leak account existence.
            return Response(status=status.HTTP_202_ACCEPTED)
        token = _tok.make_token(user.id)
        send_mail(
            subject="Password reset",
            message=f"Use this token within 1 hour: {token}",
            from_email=None,
            recipient_list=[user.email],
            fail_silently=True,
        )
        logger.info("password_reset_token_issued", extra={"user_id": str(user.id)})
        return Response(status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=["auth"], request=_ser.PasswordResetConfirmSerializer,
               responses={200: OpenApiResponse(description="Password updated")})
class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetRateThrottle]

    def post(self, request):
        ser = _ser.PasswordResetConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user_id = _tok.consume_token(ser.validated_data["token"])
        if not user_id:
            return Response({"detail": "invalid or expired token"}, status=400)
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({"detail": "invalid token"}, status=400)
        user.set_password(ser.validated_data["new_password"])
        user.save(update_fields=["password"])
        return Response({"detail": "password updated"})
