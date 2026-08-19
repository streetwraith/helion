from django import forms
from django.core.exceptions import ValidationError

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
