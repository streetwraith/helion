from functools import wraps

from django.shortcuts import redirect


def require_character(view):
    """Redirect to the character selection when no ESI character is in the session."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('esi_token'):
            return redirect('characters')
        return view(request, *args, **kwargs)
    return wrapper
