// The market browser's filters.
//
// The whole order book renders into the page and every filter runs here, so a
// filter change costs no request and the table can never disagree with itself.
// The URL is the filter state: this file is its only reader, which is why the
// view never parses those parameters. The table renders hidden and this file
// reveals it, so no row can flash and then vanish.
const SECURITY_BANDS = ['hisec', 'lowsec', 'nullsec'];

// data-hub names the trade hub an order reaches, empty when it reaches none.
// The server takes it from the orders_hub view, so a buy order parked one jump
// away with a range that covers the hub counts as reaching it. Comparing
// station ids here instead would drop exactly those orders.
function reachesKeptHub(row, hub) {
    const reached = row.attr('data-hub');
    return hub === 'all' ? reached !== '' : reached === hub;
}

function applyFilters() {
    const excluded = SECURITY_BANDS.filter(band => $(`input[name="no_${band}"]`).is(':checked'));
    const dropStructures = $('input[name="no_structures"]').is(':checked');
    const hubsOnly = $('input[name="hubs_only"]').is(':checked');
    const hub = $('#hub').val();

    $('#browse-book table').each(function() {
        const rows = $(this).find('tbody tr[data-sec]');
        let shown = 0;
        rows.each(function() {
            const row = $(this);
            const hidden = excluded.includes(row.attr('data-sec'))
                || (dropStructures && row.attr('data-structure') === '1')
                || (hubsOnly && !reachesKeptHub(row, hub));
            row.toggleClass('filtered-out', hidden);
            if (!hidden) {
                shown += 1;
            }
        });
        // Without the count an over-tight filter reads as an empty market.
        const side = this.id.replace('browse-', '');
        $(`.browse-count[data-table="${side}"]`).text(
            rows.length ? `showing ${shown} of ${rows.length}` : '');
    });
}

function storeFilters() {
    // The form serialization is the one place the parameter names live: the
    // native submit that changes the item sends exactly this, so picking a new
    // item keeps the filters.
    const query = $('#browse-controls').serialize();
    history.replaceState(null, '', query ? `?${query}` : window.location.pathname);
}

function restoreFilters() {
    const params = new URLSearchParams(window.location.search);
    $('#browse-controls input[type="checkbox"]').each(function() {
        this.checked = params.get(this.name) === '1';
    });
    const hub = params.get('hub');
    if (hub && $(`#hub option[value="${hub}"]`).length) {
        $('#hub').val(hub);
    }
}

$(document).ready(function() {
    restoreFilters();
    applyFilters();
    $('#browse-book').prop('hidden', false);
    $('#browse-controls').on('change', 'input[type="checkbox"], #hub', function() {
        applyFilters();
        storeFilters();
    });
});
