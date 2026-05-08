"""Chat session gateway views (REST). Streaming happens over WebSocket."""
from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.documents.services.fastapi_client import FastAPIClient, FastAPIClientError
from apps.workspaces.models import Workspace, WorkspaceMember

from . import serializers as _ser
from .models import ChatSession

logger = logging.getLogger(__name__)


def _bearer_token(request) -> str:
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth.lower().startswith("bearer "):
        return ""
    return auth.split(" ", 1)[1].strip()


def _user_can_access_session(user, sess: ChatSession) -> bool:
    if user.is_staff:
        return True
    if sess.created_by_id == user.id:
        return True
    if sess.workspace_id and WorkspaceMember.objects.filter(
        workspace_id=sess.workspace_id, user=user
    ).exists():
        return True
    return False


@extend_schema(tags=["chat"])
class ChatSessionListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: _ser.ChatSessionSerializer(many=True)})
    def get(self, request):
        user = request.user
        if user.is_staff:
            qs = ChatSession.objects.all()
        else:
            ws_ids = list(WorkspaceMember.objects.filter(user=user)
                          .values_list("workspace_id", flat=True))
            qs = ChatSession.objects.filter(
                Q(created_by=user) | Q(workspace_id__in=ws_ids)
            )
        return Response(_ser.ChatSessionSerializer(qs[:200], many=True).data)

    @extend_schema(request=_ser.ChatSessionCreateSerializer,
                   responses={201: _ser.ChatSessionSerializer})
    def post(self, request):
        ser = _ser.ChatSessionCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ws = None
        ws_id = ser.validated_data.get("workspace_id")
        if ws_id:
            ws = get_object_or_404(Workspace, pk=ws_id)
            if not (request.user.is_staff or WorkspaceMember.objects.filter(
                workspace=ws, user=request.user
            ).exists()):
                return Response({"detail": "not a workspace member"}, status=403)

        token = _bearer_token(request)
        payload = {
            "title": ser.validated_data.get("title") or "",
            "document_ids": [str(x) for x in ser.validated_data["document_ids"]],
        }
        if ws_id:
            payload["workspace_id"] = str(ws_id)

        async def _create():
            async with FastAPIClient(token) as c:
                return await c.create_chat_session(payload)

        try:
            upstream = async_to_sync(_create)()
        except FastAPIClientError as exc:
            return Response(
                {"detail": exc.detail, "upstream_status": exc.status_code},
                status=exc.status_code if exc.status_code < 500 else 502,
            )

        sess, _ = ChatSession.objects.update_or_create(
            id=upstream["id"],
            defaults={
                "workspace": ws,
                "created_by": request.user,
                "title": (upstream.get("title") or payload["title"])[:500],
                "document_ids": upstream.get("document_ids", payload["document_ids"]),
            },
        )
        return Response(_ser.ChatSessionSerializer(sess).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=["chat"])
class ChatSessionDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: _ser.ChatSessionDetailSerializer})
    def get(self, request, pk):
        sess = get_object_or_404(ChatSession, pk=pk)
        if not _user_can_access_session(request.user, sess):
            return Response({"detail": "forbidden"}, status=403)
        token = _bearer_token(request)

        async def _pull():
            async with FastAPIClient(token) as c:
                return await c.get_chat_session(str(sess.id))

        try:
            upstream = async_to_sync(_pull)()
        except FastAPIClientError as exc:
            return Response(
                {"detail": exc.detail, "upstream_status": exc.status_code},
                status=502,
            )
        return Response(upstream)

    @extend_schema(responses={204: OpenApiResponse(description="deleted")})
    def delete(self, request, pk):
        sess = get_object_or_404(ChatSession, pk=pk)
        if not _user_can_access_session(request.user, sess):
            return Response({"detail": "forbidden"}, status=403)
        token = _bearer_token(request)

        async def _del():
            async with FastAPIClient(token) as c:
                await c.delete_chat_session(str(sess.id))

        try:
            async_to_sync(_del)()
        except FastAPIClientError as exc:
            return Response(
                {"detail": exc.detail, "upstream_status": exc.status_code},
                status=502,
            )
        sess.delete()
        return Response(status=204)
