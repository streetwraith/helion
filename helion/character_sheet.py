"""The character sheet: attributes, skills, the skill queue, implants and the
jump clone cooldown.

Read live from ESI and held in the cache for CHARACTER_SHEET_CACHE_SECONDS.
This is the one place in the app that calls ESI while a page renders. It is
worth it here: the page is rare, the five routes are all server-cached anyway,
and the alternative - five owned tables and five scheduler feeds - would cost
far more than it saves for data nobody reads between visits.
"""
import logging
from datetime import timedelta

from django.core.cache import cache

from esi.models import Token
from evesde.models import Group, Type, TypeDogmaAttribute
from helion.providers import esi

logger = logging.getLogger(__name__)

CHARACTER_SHEET_CACHE_SECONDS = 300

SKILLS_SCOPE = 'esi-skills.read_skills.v1'
SKILLQUEUE_SCOPE = 'esi-skills.read_skillqueue.v1'
CLONES_SCOPE = 'esi-clones.read_clones.v1'
IMPLANTS_SCOPE = 'esi-clones.read_implants.v1'
SHEET_SCOPES = (SKILLS_SCOPE, SKILLQUEUE_SCOPE, CLONES_SCOPE, IMPLANTS_SCOPE)

# Index is the level, so SKILL_LEVELS[3] is "III". Level 0 shows nothing.
SKILL_LEVELS = ('', 'I', 'II', 'III', 'IV', 'V')

# Jumping to another clone locks the next jump for a day, less one hour per
# level of Infomorph Synchronizing.
JUMP_CLONE_COOLDOWN_HOURS = 24
INFOMORPH_SYNCHRONIZING_TYPE_ID = 33399

# "implantness" in the sde: slots 1 to 5 hold the attribute implants, 6 to 10
# the hardwirings. It is the order the game shows, so the sheet shows it too.
IMPLANT_SLOT_ATTRIBUTE_ID = 331


def _cache_key(character_id):
    return f'character_sheet:{character_id}'


def get_character_sheet(character_id):
    """Everything the details page shows for one character.

    Returns None when the character holds no token carrying all of
    SHEET_SCOPES, which is what an older login looks like. That case is never
    cached, so a fresh login takes effect at once.
    """
    sheet = cache.get(_cache_key(character_id))
    if sheet is not None:
        return sheet
    tokens = {scope: Token.get_token(character_id, scope) for scope in SHEET_SCOPES}
    if not all(tokens.values()):
        logger.info("character %s has no token for %s", character_id,
                    [scope for scope, token in tokens.items() if not token])
        return None
    sheet = _build_sheet(character_id, tokens)
    cache.set(_cache_key(character_id), sheet, CHARACTER_SHEET_CACHE_SECONDS)
    return sheet


def _build_sheet(character_id, tokens):
    # use_etag=False on all five: a page render always needs the body, and a
    # 304 would leave it with nothing to show.
    skills = esi.client.Skills.GetCharactersCharacterIdSkills(
        character_id=character_id, token=tokens[SKILLS_SCOPE]).result(use_etag=False)
    attributes = esi.client.Skills.GetCharactersCharacterIdAttributes(
        character_id=character_id, token=tokens[SKILLS_SCOPE]).result(use_etag=False)
    queue = esi.client.Skills.GetCharactersCharacterIdSkillqueue(
        character_id=character_id, token=tokens[SKILLQUEUE_SCOPE]).result(use_etag=False)
    clones = esi.client.Clones.GetCharactersCharacterIdClones(
        character_id=character_id, token=tokens[CLONES_SCOPE]).result(use_etag=False)
    implants = esi.client.Clones.GetCharactersCharacterIdImplants(
        character_id=character_id, token=tokens[IMPLANTS_SCOPE]).result(use_etag=False)

    names = _type_names({skill.skill_id for skill in skills.skills}
                        | {entry.skill_id for entry in queue} | set(implants))
    return {
        'attributes': attributes.model_dump(),
        'total_sp': skills.total_sp,
        'unallocated_sp': skills.unallocated_sp,
        'skill_groups': _skill_groups(skills.skills, names),
        'skill_queue': _skill_queue(queue, names),
        'implants': _implants(implants, names),
        'jump_clone_ready': _jump_clone_ready(clones.last_clone_jump_date,
                                              _skill_level(skills.skills,
                                                           INFOMORPH_SYNCHRONIZING_TYPE_ID)),
        'last_clone_jump': clones.last_clone_jump_date,
    }


def _type_names(type_ids):
    return dict(Type.objects.filter(type_id__in=type_ids).values_list('type_id', 'name'))


def _skill_level(skills, type_id):
    for skill in skills:
        if skill.skill_id == type_id:
            return skill.active_skill_level
    return 0


def _skill_groups(skills, names):
    """Trained skills as one block per inventory group, both sorted by name.

    A skill with no skill points is injected but never trained, so it says
    nothing about the character and stays off the sheet. A group left with no
    trained skill disappears with them.

    The sde ships dangling references, so a skill whose type or group is
    missing keeps its raw id rather than disappearing from the sheet.
    """
    group_ids = dict(Type.objects.filter(type_id__in=[skill.skill_id for skill in skills])
                     .values_list('type_id', 'group_id'))
    group_names = dict(Group.objects.filter(group_id__in=set(group_ids.values()))
                       .values_list('group_id', 'name'))

    groups = {}
    for skill in skills:
        if not skill.skillpoints_in_skill:
            continue
        group_id = group_ids.get(skill.skill_id)
        group = groups.setdefault(group_names.get(group_id, 'unknown'),
                                  {'name': group_names.get(group_id, 'unknown'),
                                   'skillpoints': 0, 'skills': []})
        group['skillpoints'] += skill.skillpoints_in_skill
        group['skills'].append({
            'name': names.get(skill.skill_id, str(skill.skill_id)),
            'level': skill.active_skill_level,
            'level_roman': SKILL_LEVELS[skill.active_skill_level],
            # The XML dump reads these two; the visible list shows the level only.
            'trained_level': skill.trained_skill_level,
            'skillpoints': skill.skillpoints_in_skill,
            'skill_id': skill.skill_id,
        })
    for group in groups.values():
        group['skills'].sort(key=lambda skill: skill['name'])
    return sorted(groups.values(), key=lambda group: group['name'])


def _implants(type_ids, names):
    """Implants in slot order. An implant the sde carries no slot for sorts
    last rather than claiming a slot it may not hold."""
    slots = dict(TypeDogmaAttribute.objects
                 .filter(type_id__in=type_ids, attribute_id=IMPLANT_SLOT_ATTRIBUTE_ID)
                 .values_list('type_id', 'value'))
    implants = [{'slot': int(slots[type_id]) if type_id in slots else None,
                 'name': names.get(type_id, str(type_id))}
                for type_id in type_ids]
    return sorted(implants, key=lambda implant: (implant['slot'] is None,
                                                 implant['slot'] or 0, implant['name']))


def _skill_queue(queue, names):
    """The queue in training order. A paused queue carries no dates at all."""
    return [{
        'position': entry.queue_position,
        'name': names.get(entry.skill_id, str(entry.skill_id)),
        'level': entry.finished_level,
        'level_roman': SKILL_LEVELS[entry.finished_level],
        'finish_date': entry.finish_date,
    } for entry in sorted(queue, key=lambda entry: entry.queue_position)]


def _jump_clone_ready(last_jump, infomorph_level):
    """When the next clone jump becomes possible, or None if the character has
    never jumped."""
    if last_jump is None:
        return None
    return last_jump + timedelta(hours=JUMP_CLONE_COOLDOWN_HOURS - infomorph_level)
