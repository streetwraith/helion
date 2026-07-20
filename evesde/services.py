from evesde.models import Type


def get_type_names(type_ids):
    # Batch fetch names for matching type_ids
    type_names = Type.objects.filter(type_id__in=type_ids).values("type_id", "name")
    return {item["type_id"]: item["name"] for item in type_names}
