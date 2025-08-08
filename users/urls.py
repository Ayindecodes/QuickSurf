# users/urls.py

from django.urls import path
from .views import RegisterView, LoginView, DashboardView

urlpatterns = [
    path('register/',  RegisterView.as_view(),   name='user-register'),
    path('login/',     LoginView.as_view(),      name='user-login'),
    path('dashboard/', DashboardView.as_view(),  name='user-dashboard'),
]
