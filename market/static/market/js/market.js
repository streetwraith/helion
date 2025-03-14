
$(document).ready(function(){
    $(".market").tablesorter();

    $('.item-name').on('click', '.trade-item-id', function(event) {
        event.preventDefault();
        $(this).closest('table').find('tr').removeClass('selected');
        var parent_tr = $(this).closest('tr');
        parent_tr.addClass('selected');
        $.ajax({
            url: '/market/ajax/market_open_in_game',
            type: 'POST',
            data: {
                'type_id': parent_tr.data('type-id')
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
    $('.update-history').click(function(event) {
        event.preventDefault();
        var type_id = $(this).closest('tr').data('type-id');
        var to_region = $(this).closest('table').data('to-region-id')
        var link = $(this);
        var spinner = $(this).parent().find('.loading-spinner');
        $.ajax({
            url: '/market/ajax/market_history',
            type: 'POST',
            data: {
                'type_id': type_id,
                'region_id': to_region,
            },
            dataType: 'json',
            beforeSend: function() {
                link.hide();
                spinner.show();
            },
            success: function(data) {
                $('.type-'+type_id+'.history-container').html(data.html);
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
                // Thread.sleep(2);
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
