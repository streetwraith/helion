from celery import shared_task


@shared_task
def build_character_profile(character_id: int) -> None:
    """Fetch + cache killmails for a character and compute their PvP profile.

    Wraps its body in a Redis cache lock to prevent overlapping runs (see
    market/tasks.py for the pattern). Not implemented yet — the current UI is a
    static demo.
    """
    raise NotImplementedError
