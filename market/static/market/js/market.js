
$(document).ready(function(){
    $(".market").tablesorter();

    $('.item-name').on('click', '.plus-icon, .minus-icon', function(event) {
        event.preventDefault();
        var type_id = $(this).closest('tr').data('type-id');
        var link = $(this);
        var operation = 'add'
        if(link.hasClass('minus-icon'))
            operation = 'del'
        var spinner = $(this).parent().find('.loading-spinner');
        $.ajax({
            url: '/market/ajax/trade_item_add_or_del',
            type: 'POST',
            data: {
                'type_id': type_id,
                'operation': operation,
            },
            dataType: 'json',
            beforeSend: function() {
                link.hide();
                spinner.show();
            },
            success: function(data) {
                parent = link.closest('td.item-name')
                parent.html(data.html);

                if(parent.find('.plus-icon').length > 0) {
                    parent.removeClass('item-added');
                    parent.addClass('item-deleted');
                } else {
                    parent.addClass('item-added');
                    parent.removeClass('item-deleted');
                }
            },
            error: function() {
                console.log('Error loading data!');
            },
            complete: function() {
                link.show();
                spinner.hide();
            }
        });
    });
});
