from django.shortcuts import redirect
from django.urls import reverse

class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            login_url = reverse('login')
            if request.path != login_url:
                return redirect(f"{login_url}?next={request.path}")
        response = self.get_response(request)
        return response