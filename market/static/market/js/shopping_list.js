// The shopping list page: open a saved list, and edit its items in place.
//
// Every edit writes at once and the server answers with the whole table, because
// one changed item moves the region totals and the cheapest-price marks of every
// other row.

function showShoppingError(message) {
    $('#shopping-error').text(message || '').prop('hidden', !message);
}

function postShoppingChange(payload) {
    const table = $('#shopping-table');
    $.ajax({
        url: table.data('endpoint'),
        type: 'POST',
        headers: {'X-Requested-With': 'XMLHttpRequest'},
        data: $.extend({list_id: table.data('list-id')}, payload),
        dataType: 'json',
        success: function(data) {
            table.html(data.html);
            showShoppingError('');
        },
        error: function(xhr) {
            showShoppingError((xhr.responseJSON && xhr.responseJSON.error)
                              || 'the change failed');
        }
    });
}

function bindShoppingList() {
    $('#saved-list-select').on('change', function() {
        if (this.value) {
            window.location = this.value;
        }
    });

    $('#delete-list-form').on('submit', function(event) {
        if (!window.confirm('Delete the list "' + $(this).data('list-name') + '"?')) {
            event.preventDefault();
        }
    });

    // The save button posts to another URL and creates a list, so only the
    // replace of an open list asks.
    $('#paste-form').on('submit', function(event) {
        const name = $(this).data('list-name');
        if (name && !window.confirm('Replace every item of "' + name + '"?')) {
            event.preventDefault();
        }
    });

    $('#add-item-form').on('submit', function(event) {
        event.preventDefault();
        const input = $('#type_search');
        const name = input.val().trim();
        if (!name) {
            return;
        }
        postShoppingChange({operation: 'add', name: name,
                            quantity: $('#add-quantity').val()});
        input.val('');
        $('#type_id').val('');
    });

    $('#shopping-table').on('click', '.item-remove', function() {
        postShoppingChange({operation: 'del', item_id: $(this).data('item-id')});
    });

    $('#shopping-table').on('change', '.item-quantity', function() {
        postShoppingChange({operation: 'qty', item_id: $(this).data('item-id'),
                            quantity: $(this).val()});
    });
}

$(document).ready(bindShoppingList);
