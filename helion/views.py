from django.shortcuts import render, redirect
from evesde.models import Type
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

    # No require_character here: this view is the redirect target. Without a
    # selected character the skills block is skipped and the page still lists
    # the tokens to select.
    if request.GET.get('show_skills', False) and request.session.get('esi_token'):
        character_id = request.session['esi_token']['character_id']
        token = Token.get_token(character_id, 'esi-skills.read_skills.v1')
        # use_etag=False: runs in the request path and always needs the body.
        character_skills = esi.client.Skills.GetCharactersCharacterIdSkills(
            character_id=character_id, token=token).result(use_etag=False).model_dump()
        context['character_skills'] = character_skills
        skills = Type.objects.filter(type_id__in=[skill['skill_id'] for skill in character_skills['skills']]).values('type_id', 'name')
        context['skill_names'] = {skill['type_id']: skill['name'] for skill in skills}

    return render(request, "characters.html", context=context)