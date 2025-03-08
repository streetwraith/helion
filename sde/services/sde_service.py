from sde.models import SdeTypeId

def get_type_names(type_ids):
    # Batch fetch names for matching type_ids
    type_names = SdeTypeId.objects.filter(
        type_id__in=type_ids
    ).values('type_id', 'name')
    # Map type_id to name
    type_names_dict = {item['type_id']: item['name'] for item in type_names}
    return type_names_dict