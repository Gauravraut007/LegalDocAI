from django.urls import path

from .consumers import DocumentEventsConsumer

websocket_urlpatterns = [
    path("ws/documents/<uuid:document_id>/events/",
         DocumentEventsConsumer.as_asgi()),
]
