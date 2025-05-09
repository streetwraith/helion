"""
URL configuration for helion project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from . import views

urlpatterns = [
    # path('accounts/', include('django.contrib.auth.urls')),
    # path('accounts/login/', auth_views.LoginView.as_view()),
    path('login/', auth_views.LoginView.as_view(template_name="user/login.html"), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    path('', views.index, name='helion.index'),
    path('characters/', views.characters, name='characters'),

    # url(r'^sso/', include('esi.urls', namespace='esi')),
    re_path(r'^sso/', include(('esi.urls', 'esi'), namespace='esi')),
    # path('callback', views.callback, name='auth.callback'),

    path('market/', include('market.urls')),
    path('sde/', include('sde.urls')),
    
    path('admin/', admin.site.urls),
]