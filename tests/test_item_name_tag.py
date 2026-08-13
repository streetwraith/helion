"""The shared item name component: which links and icons each render shows."""
from django.template import Context, Template


def render(tag):
    return Template("{% load item_tags %}" + tag).render(Context())


def test_the_name_opens_the_in_game_market():
    html = render("{% item_name 34 'Tritanium' %}")
    # The type id rides on the link, because the name also renders outside a row.
    assert '<a class="item-name-link" data-type-id="34" href="#">Tritanium</a>' in html
    assert "(34)" not in html


def test_history_link_shows_by_default_and_hides_on_request():
    html = render("{% item_name 34 'Tritanium' %}")
    assert 'href="/market/history?type_id=34&amp;region_id=10000002"' in html
    # The chart is ours, so the link stays in this tab.
    assert "target" not in html
    assert "/market/history" not in render("{% item_name 34 'Tritanium' show_history=False %}")


def test_history_link_follows_the_region_of_the_caller():
    html = render("{% item_name 34 'Tritanium' region_id=10000043 %}")
    assert 'href="/market/history?type_id=34&amp;region_id=10000043"' in html


def test_history_link_falls_back_to_the_forge_without_a_region():
    # A caller with no region, or with more than one, leaves it out.
    html = render("{% item_name 34 'Tritanium' region_id=None %}")
    assert 'region_id=10000002' in html


def test_add_del_icons_stay_out_unless_asked():
    html = render("{% item_name 34 'Tritanium' %}")
    assert "plus-icon" not in html and "minus-icon" not in html
    assert "loading-spinner" not in html


def test_add_icon_for_an_item_that_is_no_trade_item():
    html = render("{% item_name 34 'Tritanium' show_add_del=True %}")
    assert "plus-icon" in html and "minus-icon" not in html
    # The add/del call needs the spinner as a sibling of the icon.
    assert "loading-spinner" in html


def test_del_icon_for_a_trade_item():
    html = render("{% item_name 34 'Tritanium' show_add_del=True is_trade_item=True %}")
    assert "minus-icon" in html and "plus-icon" not in html
