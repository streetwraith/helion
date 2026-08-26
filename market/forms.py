from django import forms
from django.core.exceptions import ValidationError

from evesde import services as sde_service
from market.gas_constants import (
    GAS_CLOUD_SCOOP_II,
    GH_801,
    IMPLANT_CHOICES,
    SCOOP_CHOICES,
)
from market.models import PriceAlert

PRICE_BASIS_CHOICES = (
    ('bid', 'instant-sell (best bid)'),
    ('ask', 'list price (best ask)'),
    ('mid', 'mid'),
)

# A wormhole huffing fleet does not run longer than this. The bound exists
# because the count multiplies every figure on the page.
MAX_PROSPECTS = 10


class GasFleetForm(forms.Form):
    """The gas calculator's fleet, read from the query string.

    The harvest rate, the mining hold and the residue chance all follow from
    these controls, so the page carries no typed figure that can contradict the
    fleet. Skills sit at V, and no control changes that.
    """

    DEFAULTS = {
        'outrider': False,
        'outrider_scoop': GAS_CLOUD_SCOOP_II,
        'outrider_chipset': True,
        'outrider_implant': GH_801,
        'mindlink': True,
        'prospects': 2,
        'prospect_scoop': GAS_CLOUD_SCOOP_II,
        'prospect_chipset': True,
        'prospect_implant': GH_801,
        'basis': 'ask',
    }

    # An unticked checkbox submits nothing at all, so an absent key cannot mean
    # both "off" and "not given". The form writes this marker, which says the
    # query came from a submit and that an absent checkbox is therefore off. A
    # hand-written query string carries no marker, so every field it leaves out
    # keeps its default.
    SUBMITTED = 'submitted'
    CHECKBOXES = ('outrider', 'outrider_chipset', 'mindlink', 'prospect_chipset')

    # The controls read as three statements: the Outrider, the Prospects, and
    # what the table quotes them against. The page renders one row per group.
    FIELD_GROUPS = (
        ('outrider', 'outrider_scoop', 'outrider_chipset', 'outrider_implant',
         'mindlink'),
        ('prospects', 'prospect_scoop', 'prospect_chipset', 'prospect_implant'),
        ('basis', 'region_id'),
    )

    outrider = forms.BooleanField(required=False, label='Outrider')
    outrider_scoop = forms.TypedChoiceField(coerce=int, choices=SCOOP_CHOICES,
                                            label='Outrider scoops')
    outrider_chipset = forms.BooleanField(required=False,
                                          label='Outrider survey chipset')
    outrider_implant = forms.TypedChoiceField(coerce=int, choices=IMPLANT_CHOICES,
                                              required=False, empty_value=None,
                                              label='Outrider implant')
    mindlink = forms.BooleanField(required=False, label='Mining Foreman Mindlink')
    prospects = forms.IntegerField(min_value=0, max_value=MAX_PROSPECTS,
                                   label='Prospects')
    prospect_scoop = forms.TypedChoiceField(coerce=int, choices=SCOOP_CHOICES,
                                            label='Prospect scoops')
    prospect_chipset = forms.BooleanField(required=False,
                                          label='Prospect survey chipset')
    prospect_implant = forms.TypedChoiceField(coerce=int, choices=IMPLANT_CHOICES,
                                              required=False, empty_value=None,
                                              label='Prospect implant')
    basis = forms.ChoiceField(choices=PRICE_BASIS_CHOICES, label='Price')
    region_id = forms.TypedChoiceField(coerce=int, choices=(), label='Hub')

    def __init__(self, *args, hubs=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['region_id'].choices = [(hub.region_id, hub.name) for hub in hubs]

    @classmethod
    def from_query(cls, query, hubs, default_region_id):
        """Bind against the query string, filling an absent field with its
        default, so a bare URL still renders the table."""
        defaults = dict(cls.DEFAULTS)
        if query.get(cls.SUBMITTED):
            defaults.update(dict.fromkeys(cls.CHECKBOXES, False))
        data = {**defaults, 'region_id': default_region_id, **query.dict()}
        return cls(data, hubs=hubs)

    def rows(self):
        """The bound fields, grouped into the rows the page prints."""
        names = [name for group in self.FIELD_GROUPS for name in group]
        # A field left out of every group would vanish from the page silently,
        # and its default would then decide a figure that nobody can see.
        assert sorted(names) == sorted(self.fields), 'every field needs a row'
        return [[self[name] for name in group] for group in self.FIELD_GROUPS]

    def clean(self):
        cleaned = super().clean()
        # The empty fleet divides by zero in every figure the page shows. The
        # rule waits for a valid count, so a bad one reports itself alone.
        if 'prospects' in cleaned and not (cleaned['prospects'] or cleaned['outrider']):
            raise ValidationError('The fleet must hold at least one ship.')
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
