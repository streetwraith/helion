// The sell-to-sell hauling filter.
//
// Every deal renders into the page, so filtering on the average daily volume
// costs no request. The threshold stays here and never reaches the server: the
// search form posts and redirects, which would drop it on the way.
function applyVolumeFilter() {
    const raw = $('#min_avg_volume').val();
    const minimum = parseFloat(raw);
    $('#hauling-deals tbody tr').each(function() {
        const row = $(this);
        // A deal without history has no proven volume, so it fails any
        // threshold rather than passing on a missing number.
        const volume = parseFloat(row.attr('data-avg-volume')) || 0;
        row.toggle(isNaN(minimum) || volume >= minimum);
    });
}

$(document).ready(function() {
    // Browsers restore the typed value on a reload, so the filter runs once
    // before anyone touches the input.
    applyVolumeFilter();
    $('#min_avg_volume').on('input', applyVolumeFilter);
});
