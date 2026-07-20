from django.db import migrations

# One-time move of the non-CCP jumps_to_trade_hub values off the (later removed) sde
# solar-system table into helion-owned SystemHubJumps. Guarded so it no-ops when
# sde_solarsystem is absent (fresh builds, or after the sde app is dropped), and it
# deliberately does NOT depend on the sde app so removing that app can't break the
# migration graph. On a real DB with populated sde_solarsystem it copies the data;
# elsewhere SystemHubJumps is (re)built by `manage.py recompute_hub_jumps`.


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0007_systemhubjumps"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DO $$
                BEGIN
                    IF to_regclass('public.sde_solarsystem') IS NOT NULL THEN
                        INSERT INTO market_systemhubjumps (system_id, jumps_to_trade_hub)
                        SELECT system_id, jumps_to_trade_hub
                        FROM sde_solarsystem
                        WHERE jumps_to_trade_hub IS NOT NULL
                        ON CONFLICT (system_id) DO NOTHING;
                    END IF;
                END $$;
            """,
            reverse_sql="DELETE FROM market_systemhubjumps;",
        ),
    ]
