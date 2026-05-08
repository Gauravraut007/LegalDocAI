from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("sessions/", views.ChatSessionListCreateView.as_view(), name="sessions"),
    path("sessions/<uuid:pk>/", views.ChatSessionDetailView.as_view(), name="session-detail"),
]
