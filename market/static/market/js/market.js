
function getCookie(name) {
    var match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return match ? decodeURIComponent(match[2]) : null;
}

// ajaxSend fires for every request, even when a call defines its own
// beforeSend (which would override an ajaxSetup beforeSend).
$(document).ajaxSend(function(event, xhr, settings) {
    if (!/^(GET|HEAD|OPTIONS|TRACE)$/.test(settings.type)) {
        xhr.setRequestHeader('X-CSRFToken', getCookie('csrftoken'));
    }
});

$(document).ready(function(){
    $(".market").tablesorter();

    $('.item-name').on('click', '.item-name-link', function(event) {
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
