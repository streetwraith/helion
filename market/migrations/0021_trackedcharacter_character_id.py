from django.db import migrations, models


def fill_character_id(apps, schema_editor):
    """Resolve each tracked name to its id: from a token, else from the name cache.

    The name cache covers a character whose tokens were deleted before this
    migration ran. A name that neither source knows stops the migration: a
    guessed id would silently move a wallet out of the profit statistics.
    """
    Token = apps.get_model('esi', 'Token')
    EveName = apps.get_model('market', 'EveName')
    TrackedCharacter = apps.get_model('market', 'TrackedCharacter')
    unresolved = []
    for tracked in TrackedCharacter.objects.all():
        ids = set(Token.objects.filter(character_name=tracked.character_name)
                  .values_list('character_id', flat=True))
        if not ids:
            ids = set(EveName.objects.filter(name=tracked.character_name, category='character')
                      .values_list('entity_id', flat=True))
        if len(ids) != 1:
            unresolved.append(f'{tracked.character_name}: {sorted(ids)}')
            continue
        tracked.character_id = ids.pop()
        tracked.save(update_fields=['character_id'])
    if unresolved:
        raise RuntimeError('cannot resolve TrackedCharacter ids, fix the rows first: '
                           + '; '.join(unresolved))


class Migration(migrations.Migration):

    dependencies = [
        ('esi', '0013_squashed_0012_fix_token_type_choices'),
        ('market', '0020_shoppinglist_shoppinglistitem'),
    ]

    operations = [
        migrations.AddField(
            model_name='trackedcharacter',
            name='character_id',
            field=models.BigIntegerField(null=True),
        ),
        migrations.RunPython(fill_character_id, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='trackedcharacter',
            name='character_id',
            field=models.BigIntegerField(unique=True),
        ),
    ]
