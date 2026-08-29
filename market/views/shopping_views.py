"""The shopping list page: price a paste, and keep named lists."""
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from market.models import ShoppingList
from market.services import shopping


def shopping_list(request):
    """The paste form, and the price of a paste that nothing saved yet."""
    if request.method != 'POST':
        # An empty paste still prices: the render needs the region dropdown.
        return render(request, "market/shopping/shopping.html",
                      _context(**shopping.price_context({}, *_asset_filter(request))))

    query = request.POST.get('items', '')
    items = shopping.parse_items(query)
    return render(request, "market/shopping/shopping.html",
                  _context(items=query,
                           **shopping.price_context(items, *_asset_filter(request))))


def shopping_list_detail(request, list_id):
    """Open a saved list, or replace every item of it with a new paste."""
    saved = get_object_or_404(ShoppingList, id=list_id)
    if request.method == 'POST':
        try:
            shopping.replace_items(saved, shopping.parse_items(request.POST.get('items', '')))
        except shopping.ShoppingListError as error:
            return _rendered_list(request, saved, error=str(error))
        return redirect(_detail_url(saved, *_asset_filter(request)))
    return _rendered_list(request, saved)


def shopping_list_save(request):
    """Store the pasted items under a name, or replace the list of that name."""
    if request.method != 'POST':
        return redirect('shopping_list')

    query = request.POST.get('items', '')
    items = shopping.parse_items(query)
    asset_filter = _asset_filter(request)
    try:
        saved = shopping.save_list(request.POST.get('name', ''), items)
    except shopping.ShoppingListError as error:
        return render(request, "market/shopping/shopping.html",
                      _context(items=query, error=str(error),
                               **shopping.price_context(items, *asset_filter)))
    return redirect(_detail_url(saved, *asset_filter))


def shopping_list_delete(request, list_id):
    if request.method != 'POST':
        return redirect('shopping_list_detail', list_id=list_id)
    get_object_or_404(ShoppingList, id=list_id).delete()
    return redirect('shopping_list')


def _rendered_list(request, saved, error=None):
    items = shopping.stored_items(saved)
    # The textarea holds the list, so one paste can replace the whole of it.
    text = '\n'.join(f"{item['name']} x{item['quantity']}" for item in items.values())
    return render(request, "market/shopping/shopping.html",
                  _context(shopping_list=saved, items=text, error=error,
                           **shopping.price_context(items, *_asset_filter(request))))


def _context(**extra):
    """Every render carries the dropdown of saved lists."""
    return {'saved_lists': ShoppingList.objects.all(), **extra}


def _asset_filter(request):
    """What the assets column counts: (region, fitted items too).

    The form posts both controls with the paste, and the link of an open list
    carries them in the query.
    """
    return (shopping.asset_region_id(
                request.POST.get('assets_region') or request.GET.get('assets_region')),
            bool(request.POST.get('assets_fitted') or request.GET.get('assets_fitted')))


def _detail_url(saved, asset_region_id, asset_fitted):
    """The list's own link, which carries the two controls over the redirect."""
    url = reverse('shopping_list_detail', kwargs={'list_id': saved.id})
    if asset_region_id is None:
        return url
    return f'{url}?assets_region={asset_region_id}' + ('&assets_fitted=1' if asset_fitted else '')
