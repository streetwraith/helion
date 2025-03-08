$(document).ready(function() {
    $('.history-last-price').on('click', 'a', function(event) {
      event.preventDefault();
      var type_id = $(this).closest('tr').data('type-id');
      $.ajax({
          url: '/market/ajax/transaction_history',
          type: 'GET',
          data: {
              'type_id': type_id,
          },
          dataType: 'json',
          beforeSend: function() {
          },
          success: function(data) {
            openDialog(event, data.html);
          },
          error: function() {
              console.log('Error loading data!');
          },
          complete: function() {
          }
      });
    });
});