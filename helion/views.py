from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.contrib.auth import logout

from esi.decorators import token_required
from helion.providers import esi
from esi.models import Token
import os
import logging

logger = logging.getLogger(__name__)

def index(request):
    context = {}
    return render(request, "index.html", context)

def characters(request, *args, **kwargs):
    if request.method == 'POST':
        if request.POST.get("_add", False):
            from esi.views import sso_redirect
            return sso_redirect(request, scopes=os.getenv('ESI_CLIENT_SCOPE'), return_to='characters')

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

    context = {}
    tokens = (
        Token.objects.filter(user__pk=request.user.pk).require_valid()
    )
    if tokens.exists():
        token_output = []
        _characters = set()
        for t in tokens:
            if t.character_name in _characters:
                continue
            token_output.append(t)
            _characters.add(t.character_name)
        context['tokens'] = token_output

    return render(request, "characters.html", context=context)