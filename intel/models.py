from django.db import models

# Structure only — no migrations generated yet. The demo UI runs entirely off
# intel/mock_data.py and never touches these tables. Wire up + migrate when the
# real fetch/analysis pipeline lands.


class Killmail(models.Model):
    """Immutable cache of one enriched killmail (zKillboard + ESI).

    zKillboard's characterID kills endpoint already returns the full killmail
    inline (victim, attackers, zkb), so `data` holds that whole object. Region /
    constellation / security are denormalized from the SDE at insert time so the
    per-window space-type queries don't have to join on every read.
    """

    killmail_id = models.BigIntegerField(primary_key=True)
    killmail_hash = models.CharField(max_length=64)
    killmail_time = models.DateTimeField(db_index=True)
    solar_system_id = models.BigIntegerField()
    region_id = models.BigIntegerField(db_index=True)
    constellation_id = models.BigIntegerField(db_index=True)
    security = models.FloatField()
    data = models.JSONField()


class CharacterKill(models.Model):
    """Join of a profiled character to a killmail they were an attacker on.

    Powers the windowed metrics: filter by (character_id, killmail_time), then
    read the pre-extracted hot fields without rescanning the killmail JSON.
    """

    character_id = models.BigIntegerField()
    killmail = models.ForeignKey(Killmail, on_delete=models.CASCADE)
    killmail_time = models.DateTimeField()
    is_solo = models.BooleanField()
    is_final_blow = models.BooleanField()
    character_ship_type_id = models.BigIntegerField()
    victim_ship_type_id = models.BigIntegerField()

    class Meta:
        unique_together = ('character_id', 'killmail')
        indexes = [models.Index(fields=['character_id', 'killmail_time'])]
