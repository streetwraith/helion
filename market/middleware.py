from django.utils.deprecation import MiddlewareMixin

class LogRequestMiddleware(MiddlewareMixin):
    def process_request(self, request):
        print(f"Handling request path: {request.path}")
        return None