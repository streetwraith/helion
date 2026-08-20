"""The price alerts page: list, create, edit and delete.

Every mutation is a POST followed by a redirect, so a reload never repeats one.
The page shows what the database holds, like every other page here: an alert
belongs to the app, not to a login or to a character.
"""
from django.shortcuts import get_object_or_404, redirect, render

from evesde import services as sde_service
from market.forms import PriceAlertForm
from market.models import PriceAlert
from market.services import market_service


def _alert_rows(region_names):
    """Every alert with its item name resolved, sorted the way it reads."""
    alerts = list(PriceAlert.objects.all())
    type_names = sde_service.get_type_names({alert.type_id for alert in alerts})
    rows = [
        {
            'alert': alert,
            'name': type_names.get(alert.type_id, str(alert.type_id)),
            'region_name': (region_names.get(alert.region_id, str(alert.region_id))
                            if alert.region_id else 'any region'),
            'triggered_region_name': region_names.get(alert.triggered_region_id, ''),
        }
        for alert in alerts
    ]
    return sorted(rows, key=lambda row: (row['name'].casefold(), row['alert'].id))


def market_alerts(request):
    region_names = market_service.region_names()
    region_options = market_service.region_options(region_names)
    # An edit binds the same form to an existing row, so the page needs one
    # template and one POST target instead of a second create-or-update path.
    edit_id = (request.GET.get('edit') or '').strip()
    instance = get_object_or_404(PriceAlert, pk=edit_id) if edit_id else None

    if request.method == 'POST':
        alert_id = (request.POST.get('alert_id') or '').strip()
        instance = get_object_or_404(PriceAlert, pk=alert_id) if alert_id else None
        form = PriceAlertForm(request.POST, instance=instance, region_options=region_options)
        if form.is_valid():
            form.save()
            return redirect('market_alerts')
    else:
        form = PriceAlertForm(instance=instance, region_options=region_options)

    return render(request, 'market/alerts/alerts.html', {
        'form': form,
        'editing': instance,
        # The search box shows names, not ids, so an edit has to re-resolve one.
        'editing_type_name': (sde_service.get_type_names([instance.type_id]).get(instance.type_id)
                              if instance else None),
        # Not alert_rows: the context processor owns that name for the bar, which
        # base.html includes into this page like into every other.
        'page_alerts': _alert_rows(region_names),
    })


def market_alert_delete(request, alert_id):
    if request.method != 'POST':
        return redirect('market_alerts')
    get_object_or_404(PriceAlert, pk=alert_id).delete()
    return redirect('market_alerts')
