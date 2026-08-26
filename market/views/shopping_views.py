"""The shopping list page: price a paste, and keep named lists."""
from django.shortcuts import get_object_or_404, redirect, render

from market.models import ShoppingList
from market.services import shopping


def shopping_list(request):
    """The paste form, and the price of a paste that nothing saved yet."""
    if request.method != 'POST':
        return render(request, "market/shopping/shopping.html", _context())

    query = request.POST.get('items', '')
    items = shopping.parse_items(query)
    return render(request, "market/shopping/shopping.html",
                  _context(items=query, **shopping.price_context(items)))


def shopping_list_detail(request, list_id):
    """Open a saved list, or replace every item of it with a new paste."""
    saved = get_object_or_404(ShoppingList, id=list_id)
    if request.method == 'POST':
        try:
            shopping.replace_items(saved, shopping.parse_items(request.POST.get('items', '')))
        except shopping.ShoppingListError as error:
            return _rendered_list(request, saved, error=str(error))
        return redirect('shopping_list_detail', list_id=saved.id)
    return _rendered_list(request, saved)


def shopping_list_save(request):
    """Store the pasted items under a name, or replace the list of that name."""
    if request.method != 'POST':
        return redirect('shopping_list')

    query = request.POST.get('items', '')
    items = shopping.parse_items(query)
    try:
        saved = shopping.save_list(request.POST.get('name', ''), items)
    except shopping.ShoppingListError as error:
        return render(request, "market/shopping/shopping.html",
                      _context(items=query, error=str(error),
                               **shopping.price_context(items)))
    return redirect('shopping_list_detail', list_id=saved.id)


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
                           **shopping.price_context(items)))


def _context(**extra):
    """Every render carries the dropdown of saved lists."""
    return {'saved_lists': ShoppingList.objects.all(), **extra}
