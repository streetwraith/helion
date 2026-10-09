// Opens the in-game market window for an item name link. Loaded on every page,
// because the header ticker carries these links too.
$(document).ready(function() {
    // Delegated from the document, not from the enclosing cell: the undercut
    // banner builds the same link after page load, outside any table.
    $(document).on('click', '.item-name-link', function(event) {
        event.preventDefault();
        // The link carries the type id, because the item name also renders
        // outside a row (a table caption), where there is no row to read it from.
        var type_id = $(this).data('type-id');
        $(this).closest('table').find('tr').removeClass('selected');
        $(this).closest('tr').addClass('selected');

        $.ajax({
            url: '/market/ajax/market_open_in_game',
            type: 'POST',
            data: {
                'type_id': type_id
            },
            dataType: 'json',
            success: function(data) {
                console.log(data.message);
            },
            error: function() {
                console.log('Error loading data!');
            }
        });
    });
});
