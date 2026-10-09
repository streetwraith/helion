// Every page can POST (the header ticker opens the market window), so the
// CSRF header is set here once, not per page: a second ajaxSend handler
// would send the header twice. The token comes from base.html, not from the
// cookie: Django sets that cookie only once some page renders a token.
// ajaxSend fires for every request, even when a call defines its own
// beforeSend (which would override an ajaxSetup beforeSend).
$(document).ajaxSend(function(event, xhr, settings) {
    if (!/^(GET|HEAD|OPTIONS|TRACE)$/.test(settings.type)) {
        xhr.setRequestHeader('X-CSRFToken', $('meta[name="csrf-token"]').attr('content'));
    }
});
