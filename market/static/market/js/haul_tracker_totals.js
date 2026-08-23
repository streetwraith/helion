// The haul tracker's summary.
//
// A haul is reconstructed from one day of buys, so it picks up rows the trader
// did not haul. Unticking one changes nothing per row - every figure there is
// independent - so the totals are the only thing to redo, and the browser can
// do that without a request.
function iskFormat(value) {
    return value.toLocaleString('en-US', {minimumFractionDigits: 2,
                                          maximumFractionDigits: 2});
}

function updateHaulTotals() {
    const totals = {cost: 0, cost_sold: 0, revenue: 0, tax: 0, fee: 0, net: 0};
    let items = 0;

    $('#haul-rows tbody tr').each(function() {
        const row = $(this);
        if (!row.find('.haul-pick').prop('checked')) {
            return;
        }
        items += 1;
        for (const key of Object.keys(totals)) {
            totals[key] += parseFloat(row.attr('data-' + key.replace('_', '-'))) || 0;
        }
    });

    for (const [key, value] of Object.entries(totals)) {
        $('#haul-totals [data-total="' + key + '"]').text(iskFormat(value));
    }
    $('#haul-totals [data-total="items"]').text(items);
    // Margin measures the units that sold, so it divides by their buy cost and
    // not by the whole haul: unsold stock has not failed, it has not finished.
    $('#haul-totals [data-total="margin"]').text(
        totals.cost_sold ? (totals.net / totals.cost_sold * 100).toFixed(1) + '%' : '');
    $('#haul-totals [data-total="sold_percent"]').text(
        totals.cost ? (totals.cost_sold / totals.cost * 100).toFixed(1) + '%' : '');
}

$(document).ready(function() {
    updateHaulTotals();
    $('#haul-rows').on('change', '.haul-pick', updateHaulTotals);
});
