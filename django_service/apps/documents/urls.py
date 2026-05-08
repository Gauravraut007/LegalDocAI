from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.DocumentListCreateView.as_view(), name="list-create"),
    path("<uuid:pk>/", views.DocumentDetailView.as_view(), name="detail"),
    path("<uuid:pk>/status/", views.DocumentStatusView.as_view(), name="status"),
]
