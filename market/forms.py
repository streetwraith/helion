from django import forms
from django.core.exceptions import ValidationError

from evesde import services as sde_service
from market.models import PriceAlert

PRICE_BASIS_CHOICES = (
    ('bid', 'instant-sell (best bid)'),
    ('ask', 'list price (best ask)'),
    ('mid', 'mid'),
)


class GasFleetForm(forms.Form):
    """The gas calculator's fleet setup, read from the query string.

    Every field carries bounds because three of them are divisors: a zero
    harvest rate, a zero hold and a residue chance of -100 each divide by zero.
    """

    DEFAULTS = {
        'boost_rate': 0,
        'frigate_rate': 5.4,
        'hold': 25000,
        'residue_chance': 27.2,
        'basis': 'ask',
    }

    boost_rate = forms.FloatField(min_value=0, label='Boosting ship harvest rate (m3/s)')
    frigate_rate = forms.FloatField(min_value=0, label='Combined frigate harvest rate (m3/s)')
    hold = forms.FloatField(min_value=1, label='Combined mining hold (m3)')
    residue_chance = forms.FloatField(min_value=0, max_value=100,
                                      label='Average residue chance (%)')
    basis = forms.ChoiceField(choices=PRICE_BASIS_CHOICES, label='Price')
    region_id = forms.TypedChoiceField(coerce=int, choices=(), label='Hub')

    def __init__(self, *args, hubs=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['region_id'].choices = [(hub.region_id, hub.name) for hub in hubs]

    @classmethod
    def from_query(cls, query, hubs, default_region_id):
        """Bind against the query string, filling an absent field with its
        default, so a bare URL still renders the table."""
        data = {**cls.DEFAULTS, 'region_id': default_region_id, **query.dict()}
        return cls(data, hubs=hubs)

    def clean(self):
        cleaned = super().clean()
        boost_rate = cleaned.get('boost_rate')
        frigate_rate = cleaned.get('frigate_rate')
        if boost_rate is not None and frigate_rate is not None:
            if boost_rate + frigate_rate <= 0:
                raise ValidationError('The total harvest rate must be above zero.')
        return cleaned


# side and operator reach the user as one control, because a trader reads
# "ask < 4.00" as one statement rather than as two independent choices.
CONDITIONS = {
    'bid>=': (PriceAlert.Side.BID, PriceAlert.Operator.GTE),
    'bid<': (PriceAlert.Side.BID, PriceAlert.Operator.LT),
    'ask<': (PriceAlert.Side.ASK, PriceAlert.Operator.LT),
    'ask>=': (PriceAlert.Side.ASK, PriceAlert.Operator.GTE),
}

CONDITION_CHOICES = [(key, f'{side} {operator}') for key, (side, operator) in CONDITIONS.items()]


class PriceAlertForm(forms.ModelForm):
    """Create or edit one price alert.

    `type_id` is hidden and comes from the shared item search box, which on this
    page must not submit the form: region, condition and price still need
    filling in.
    """

    # No labels anywhere in this form: the template writes them, so a label here
    # would be dead configuration that looks authoritative.
    condition = forms.ChoiceField(choices=CONDITION_CHOICES)

    class Meta:
        model = PriceAlert
        fields = ['type_id', 'region_id', 'hubs_only', 'threshold']
        # The id is fixed, not Django's id_type_id: type_search.js writes the
        # chosen type into #type_id, the same id the other two pages hand-write.
        widgets = {'type_id': forms.HiddenInput(attrs={'id': 'type_id'})}

    def __init__(self, *args, region_options=(), **kwargs):
        super().__init__(*args, **kwargs)
        # An empty choice means every ingested region, so the field is optional
        # and coerces the blank to None rather than to 0.
        self.fields['region_id'] = forms.TypedChoiceField(
            coerce=int, choices=[('', 'any region'), *region_options],
            required=False, empty_value=None)
        if self.instance.pk and 'condition' not in self.data:
            self.fields['condition'].initial = f'{self.instance.side}{self.instance.operator}'

    def _get_validation_exclusions(self):
        # side and operator carry no form field, so the base class excludes them
        # from model validation - and the unique constraint that names them then
        # never runs. clean() has already put both on the instance.
        return super()._get_validation_exclusions() - {'side', 'operator'}

    def clean_type_id(self):
        type_id = self.cleaned_data['type_id']
        # The hidden field is as untrusted as any other input, and an alert on an
        # item that does not exist would sit silent forever.
        if not sde_service.get_type_names([type_id]):
            raise ValidationError('No such item.')
        return type_id

    def clean(self):
        cleaned = super().clean()
        condition = cleaned.get('condition')
        if condition:
            # Set before _post_clean so the unique constraint sees both columns.
            self.instance.side, self.instance.operator = CONDITIONS[condition]
        return cleaned
