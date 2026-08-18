// The assets table filters.
//
// Every asset row renders into the page, so every filter runs here and a change
// costs no request. The count is what tells an over-tight filter from an empty
// hangar. An owner is a character or a corporation.
function applyFilters() {
    const owner = $('#owner').val();
    const category = $('#category').val();
    const item = $('#item').val().trim().toLowerCase();
    const rows = $('#assets tbody tr[data-owner]');
    let shown = 0;

    rows.each(function() {
        const row = $(this);
        const keep = (owner === '' || row.attr('data-owner') === owner)
            && (category === '' || row.attr('data-category') === category)
            && (item === '' || row.find('td.item').text().toLowerCase().includes(item));
        row.toggle(keep);
        if (keep) {
            shown += 1;
        }
    });

    $('#asset-count').text(rows.length ? `showing ${shown} of ${rows.length}` : '');
}

$(document).ready(function() {
    applyFilters();
    $('#owner, #category').on('change', applyFilters);
    $('#item').on('input', applyFilters);
});
