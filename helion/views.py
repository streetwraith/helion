from django.conf import settings
from django.http import Http404
from django.shortcuts import render, redirect
from django.utils import timezone
from aiopenapi3.errors import HTTPError
from esi.errors import TokenError
from esi.models import Token
from helion.character_sheet import get_character_sheet
import logging

logger = logging.getLogger(__name__)


def _active_character_id(request):
    return (request.session.get('esi_token') or {}).get('character_id')


def _token_for(request, character_id):
    # One character can hold several tokens, one per SSO login. The newest
    # carries the scopes of the newest authorisation, so it wins.
    return (Token.objects.filter(user=request.user, character_id=character_id)
            .order_by('-pk').first())


def _shown_character(request):
    """The character this page details.

    The query string names it. Without one the page falls back to the active
    character, and to nothing at all when no character is active - that is the
    state a first login and the SSO return both land in.
    """
    requested = request.GET.get('character')
    if requested is None:
        active_id = _active_character_id(request)
        token = _token_for(request, active_id) if active_id else None
        if token is None:
            return None
    else:
        try:
            character_id = int(requested)
        except ValueError:
            raise Http404("no such character")
        token = _token_for(request, character_id)
        if token is None:
            raise Http404("no such character")
    return {
        'character_id': token.character_id,
        'name': token.character_name,
        'token_pk': token.pk,
    }

def index(request):
    context = {}
    return render(request, "index.html", context)

def characters(request, *args, **kwargs):
    if request.method == 'POST':
        if request.POST.get("_add", False):
            from esi.views import sso_redirect
            return sso_redirect(request, scopes=settings.ESI_CLIENT_SCOPE, return_to='characters')

        token_pk = request.POST.get('_token', None)
        if token_pk:
            try:
                token = Token.objects.get(pk=token_pk)
                if (((token.user and token.user == request.user) or not token.user)
                    and Token.objects.filter(pk=token_pk).require_valid().exists()):
                    request.session['esi_token'] = {
                        'token_pk': token.pk,
                        'character_id': token.character_id,
                        'character_name': token.character_name,
                    }
                    logger.debug("Token selected: %s", token_pk)
            except Token.DoesNotExist:
                logger.debug("Token %s not found.", token_pk)
            return redirect('characters')

    # No require_character here: this view is the redirect target. Without a
    # character the sheet is skipped and the page still offers the login that
    # produces one.
    character = _shown_character(request)
    context = {'character': character, 'show_skills': bool(request.GET.get('show_skills'))}
    if character:
        try:
            sheet = get_character_sheet(character['character_id'])
            context['sheet'] = sheet
            # Derived per request, not in the sheet: the sheet is cached for
            # minutes and this flag flips on a second.
            ready = sheet['jump_clone_ready'] if sheet else None
            context['jump_clone_available'] = ready is not None and ready <= timezone.now()
        except (TokenError, HTTPError) as exc:
            # One dead token or one ESI outage must not take the page with it:
            # make active, add and logout all still have to work here.
            logger.warning("character sheet for %s failed: %r",
                           character['character_id'], exc)
            context['sheet_error'] = repr(exc)

    return render(request, "characters.html", context=context)