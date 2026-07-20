from django.db import migrations

# One-time move of the non-CCP `jumps_to_trade_hub` values off the (soon read-only)
# sde solar-system table into the helion-owned SystemHubJumps. Depends on the sde
# app so sde_solarsystem exists when this runs; harmless (0 rows) if it's empty.


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0007_systemhubjumps"),
        ("sde", "0003_sdetypeid_portion_size"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                INSERT INTO market_systemhubjumps (system_id, jumps_to_trade_hub)
                SELECT system_id, jumps_to_trade_hub
                FROM sde_solarsystem
                WHERE jumps_to_trade_hub IS NOT NULL
                ON CONFLICT (system_id) DO NOTHING;
            """,
            reverse_sql="DELETE FROM market_systemhubjumps;",
        ),
    ]
