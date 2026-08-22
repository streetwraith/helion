from django.shortcuts import redirect
from django.urls import reverse

class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            login_url = reverse('login')
            # healthz stays public: a redirect to the login page answers 200 and
            # would report a dead datastore as healthy.
            if request.path not in (login_url, reverse('healthz')):
                return redirect(f"{login_url}?next={request.path}")
        response = self.get_response(request)
        return response