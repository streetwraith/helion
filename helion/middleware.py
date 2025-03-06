from django.shortcuts import redirect
from django.urls import reverse

class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        print(f"middleware request: {request.user.is_authenticated}")
        if not request.user.is_authenticated:
            # List of public paths like '/login/', '/about/', etc
            public_paths = [
                reverse('login'),  # You might need to adjust this as per your named URL for login
                # reverse('accounts/login'),
                # reverse('/accounts/login/'),
                # reverse('/accounts/login'),
            ]

            if not request.path in public_paths:
                return  redirect("/login/?next=%s" % request.path)
        response = self.get_response(request)
        return response