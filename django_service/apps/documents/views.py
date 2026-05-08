"""Documents gateway views."""
from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, parsers, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.documents.services.fastapi_client import FastAPIClient, FastAPIClientError
from apps.workspaces.models import Workspace, WorkspaceMember
from common.pagination import StandardResultsPagination

from . import serializers as _ser
from .models import Document, DocStatus, DocType

logger = logging.getLogger(__name__)


def _user_workspace_ids(user) -> list:
    return list(
        WorkspaceMember.objects.filter(user=user).values_list("workspace_id", flat=True)
    )


def _bearer_token(request) -> str:
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth.lower().startswith("bearer "):
        return ""
    return auth.split(" ", 1)[1].strip()


def _ensure_workspace_access(user, workspace_id) -> Workspace:
    ws = get_object_or_404(Workspace, pk=workspace_id)
    if user.is_staff or WorkspaceMember.objects.filter(
        workspace=ws, user=user
    ).exists():
        return ws
    from rest_framework.exceptions import PermissionDenied as DRFDenied

    raise DRFDenied("not a member of this workspace")


def _map_doc_type(value: str) -> str:
    try:
        return DocType(value).value
    except ValueError:
        return DocType.UNKNOWN.value


def _map_doc_status(value: str) -> str:
    try:
        return DocStatus(value).value
    except ValueError:
        return DocStatus.UPLOADED.value


@extend_schema(tags=["documents"])
class DocumentListCreateView(generics.GenericAPIView):
    serializer_class = _ser.DocumentSerializer
    pagination_class = StandardResultsPagination
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    queryset = Document.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Document.objects.none()
        user = self.request.user
        if user.is_staff:
            qs = Document.objects.all()
        else:
            qs = Document.objects.filter(workspace_id__in=_user_workspace_ids(user))
        ws_id = self.request.query_params.get("workspace_id")
        if ws_id:
            qs = qs.filter(workspace_id=ws_id)
        st = self.request.query_params.get("status")
        if st:
            qs = qs.filter(status=st)
        dt = self.request.query_params.get("doc_type")
        if dt:
            qs = qs.filter(doc_type=dt)
        return qs.order_by("-created_at")

    @extend_schema(
        parameters=[
            OpenApiParameter("workspace_id", str, OpenApiParameter.QUERY),
            OpenApiParameter("status", str, OpenApiParameter.QUERY),
            OpenApiParameter("doc_type", str, OpenApiParameter.QUERY),
        ],
        responses={200: _ser.DocumentSerializer(many=True)},
    )
    def get(self, request):
        page = self.paginate_queryset(self.get_queryset())
        ser = self.get_serializer(page, many=True)
        return self.get_paginated_response(ser.data)

    @extend_schema(request=_ser.DocumentUploadSerializer,
                   responses={202: _ser.DocumentSerializer})
    def post(self, request):
        ser = _ser.DocumentUploadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ws = _ensure_workspace_access(request.user, ser.validated_data["workspace_id"])
        upload = ser.validated_data["file"]
        title = ser.validated_data.get("title") or upload.name
        token = _bearer_token(request)

        async def _do():
            async with FastAPIClient(token) as client:
                payload = await client.upload(
                    filename=upload.name,
                    content_type=upload.content_type,
                    file_bytes=upload.read(),
                    workspace_id=str(ws.id),
                )
            return payload

        try:
            payload = async_to_sync(_do)()
        except FastAPIClientError as exc:
            return Response(
                {"detail": exc.detail, "upstream_status": exc.status_code},
                status=exc.status_code if exc.status_code < 500 else 502,
            )

        doc, _ = Document.objects.update_or_create(
            id=payload["document_id"],
            defaults={
                "workspace": ws,
                "uploaded_by": request.user,
                "title": title[:500],
                "original_filename": payload.get("original_filename") or upload.name,
                "size_bytes": int(payload.get("size_bytes", 0)),
                "sha256": payload.get("sha256", ""),
                "status": _map_doc_status(payload.get("status", "uploaded")),
            },
        )
        return Response(_ser.DocumentSerializer(doc).data,
                        status=status.HTTP_202_ACCEPTED)


def _user_can_access_doc(user, doc: Document) -> bool:
    if user.is_staff:
        return True
    return WorkspaceMember.objects.filter(
        workspace=doc.workspace, user=user
    ).exists()


@extend_schema(tags=["documents"])
class DocumentDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: _ser.DocumentSerializer})
    def get(self, request, pk):
        doc = get_object_or_404(Document, pk=pk)
        if not _user_can_access_doc(request.user, doc):
            return Response({"detail": "forbidden"}, status=403)
        # Lazy-pull status from upstream so we can refresh the mirror.
        token = _bearer_token(request)

        async def _pull():
            async with FastAPIClient(token) as c:
                return await c.get(str(doc.id))

        try:
            payload = async_to_sync(_pull)()
            new_status = _map_doc_status(payload.get("status", doc.status))
            new_type = _map_doc_type(payload.get("doc_type", doc.doc_type))
            page_count = payload.get("page_count")
            updates = {}
            if new_status != doc.status:
                updates["status"] = new_status
            if new_type != doc.doc_type:
                updates["doc_type"] = new_type
            if page_count and page_count != doc.page_count:
                updates["page_count"] = page_count
            if updates:
                for k, v in updates.items():
                    setattr(doc, k, v)
                doc.save(update_fields=list(updates.keys()) + ["updated_at"])
        except FastAPIClientError as exc:
            logger.warning("upstream_pull_failed", extra={"doc_id": str(doc.id),
                                                           "status": exc.status_code})
        return Response(_ser.DocumentSerializer(doc).data)

    @extend_schema(responses={204: OpenApiResponse(description="deleted")})
    def delete(self, request, pk):
        doc = get_object_or_404(Document, pk=pk)
        if not _user_can_access_doc(request.user, doc):
            return Response({"detail": "forbidden"}, status=403)
        token = _bearer_token(request)

        async def _del():
            async with FastAPIClient(token) as c:
                await c.delete(str(doc.id))

        try:
            async_to_sync(_del)()
        except FastAPIClientError as exc:
            return Response(
                {"detail": exc.detail, "upstream_status": exc.status_code},
                status=502,
            )
        doc.delete()
        return Response(status=204)


@extend_schema(tags=["documents"])
class DocumentStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: OpenApiResponse(description="upstream status snapshot")})
    def get(self, request, pk):
        doc = get_object_or_404(Document, pk=pk)
        if not _user_can_access_doc(request.user, doc):
            return Response({"detail": "forbidden"}, status=403)
        token = _bearer_token(request)

        async def _pull():
            async with FastAPIClient(token) as c:
                return await c.get(str(doc.id))

        try:
            payload = async_to_sync(_pull)()
        except FastAPIClientError as exc:
            return Response(
                {"detail": exc.detail, "upstream_status": exc.status_code},
                status=502,
            )
        return Response({
            "id": str(doc.id),
            "status": payload.get("status"),
            "doc_type": payload.get("doc_type"),
            "page_count": payload.get("page_count"),
            "latest_job": payload.get("latest_job"),
        })
