from django.shortcuts import redirect
from django.urls import reverse

class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            public_paths = [
                reverse('login'),
            ]

            if request.path not in public_paths:
                return redirect("/login/?next=%s" % request.path)
        response = self.get_response(request)
        return response