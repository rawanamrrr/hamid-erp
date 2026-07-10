from django.urls import path, include
from django.shortcuts import redirect
from django.http import HttpRequest
from . import views
from .admin import licensing_admin


# SECRET DEVELOPER-ONLY PATH - CHANGE THIS TO YOUR OWN RANDOM/SECRET PATH!
# THIS IS YOUR SECRET KEY - ONLY YOU KNOW IT!
SECRET_DEV_PATH = "dev-licensing-84761234Aa"

# Wrap all admin urls with our login requirement
def dev_login_required(view_func):
    """Simple decorator to require developer login"""
    def wrapper(request, *args, **kwargs):
        if not request.session.get('licensing_dev_logged_in', False):
            return redirect('licensing_dev_login')
        return view_func(request, *args, **kwargs)
    return wrapper

# Create protected admin
original_get_urls = licensing_admin.get_urls
def protected_get_urls():
    urlpatterns = original_get_urls()
    for urlp in urlpatterns:
        if hasattr(urlp, 'callback'):
            urlp.callback = dev_login_required(urlp.callback)
    return urlpatterns
licensing_admin.get_urls = protected_get_urls


urlpatterns = [
    path(f'{SECRET_DEV_PATH}/login/', views.dev_login_view, name='licensing_dev_login'),
    path(f'{SECRET_DEV_PATH}/logout/', views.dev_logout_view, name='licensing_dev_logout'),
    path(f'{SECRET_DEV_PATH}/', licensing_admin.urls),
    path('activate/', views.activation_view, name='license_activation'),
    path('api/status/', views.licensing_status, name='license_status_api'),
]