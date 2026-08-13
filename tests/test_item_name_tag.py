"""The shared item name component: which links and icons each render shows."""
from django.template import Context, Template


def render(tag):
    return Template("{% load item_tags %}" + tag).render(Context())


def test_the_name_opens_the_in_game_market():
    html = render("{% item_name 34 'Tritanium' %}")
    # The type id rides on the link, because the name also renders outside a row.
    assert '<a class="item-name-link" data-type-id="34" href="#">Tritanium</a>' in html
    assert "(34)" not in html


def test_evetycoon_link_shows_by_default_and_hides_on_request():
    assert "https://evetycoon.com/market/34/history" in render("{% item_name 34 'Tritanium' %}")
    assert "evetycoon" not in render("{% item_name 34 'Tritanium' show_evetycoon=False %}")


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
