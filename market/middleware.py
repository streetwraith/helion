from django.utils.deprecation import MiddlewareMixin
from celery.app.control import Inspect
from helion.celery import app

class LogRequestMiddleware(MiddlewareMixin):
    def process_request(self, request):
        print(f"Handling request path: {request.path}")

        inspect = app.control.inspect()
        active_tasks = inspect.active()
        print(f'active_tasks: {active_tasks}')
        return None