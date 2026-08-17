// The trade hub's row filters and column toggles.
//
// Both live in one file because they interact: hiding a column clears and
// disables the filter that reads it, so no number the page does not show can
// shrink the table.
//
// The checkboxes sit in a header row of their own, between the group row and the
// labels, and each table carries a copy of that row. A box hides with its own
// column, so nothing inside the table can bring a column back. That is the
// design: none of this persists, and a reload shows every column again.
const TABLE_IDS = ['trade-items', 'trade-extras'];

// Filter input -> the data-col key of the columns it reads. The o48 box reads
// one column per side, and hiding either side is enough to disable it.
const FILTER_COLUMNS = {'max-o48': 'o48', 'min-hvol-other': 'hvol-other'};

// The item name is the row's identity, and the click that opens the in-game
// market window lives in it, so its column carries no checkbox.
const FIXED_COLUMN = 0;

const TOGGLE_ROW_CLASS = 'column-toggles';

const hiddenColumns = new Set();

// Every header cell of both tables, with the column range it covered before any
// hiding. Captured once: applyColumns rewrites the colspan of a group cell, so
// reading the spans again would compute the ranges from the shrunk values.
const headerLayout = [];

// data-col key -> the column indexes carrying it, so a filter can ask whether
// the columns it reads are still on screen.
const keyedColumns = {};

function tradeHubTables() {
    return TABLE_IDS.map(id => document.getElementById(id)).filter(table => table);
}

// Each cell of a header row, with the column range it covers. One rule then
// serves every header row of the head and the foot: a cell shrinks to its
// visible columns and goes when none are left.
function spannedCells(row) {
    const cells = [];
    let index = 0;
    for (const cell of row.cells) {
        const span = cell.colSpan || 1;
        cells.push({cell: cell, start: index, span: span});
        index += span;
    }
    return cells;
}

function headerRows(table) {
    return [...(table.tHead ? table.tHead.rows : []),
            ...(table.tFoot ? table.tFoot.rows : [])];
}

function columnRange(entry) {
    const indexes = [];
    for (let index = entry.start; index < entry.start + entry.span; index += 1) {
        indexes.push(index);
    }
    return indexes;
}

function visibleCount(start, span) {
    let visible = 0;
    for (let index = start; index < start + span; index += 1) {
        if (!hiddenColumns.has(index)) {
            visible += 1;
        }
    }
    return visible;
}

function buildToggles() {
    tradeHubTables().forEach(table => {
        // Read the labels before the insert, which makes them the third row.
        const labels = spannedCells(table.tHead.rows[1]);
        const row = table.tHead.insertRow(1);
        // tablesorter skips a row carrying this class when it builds its
        // headers, so a checkbox cell never becomes a sort handle.
        row.className = `${TOGGLE_ROW_CLASS} tablesorter-ignoreRow`;
        labels.forEach(column => {
            const cell = document.createElement('th');
            cell.colSpan = column.span;
            if (column.start !== FIXED_COLUMN) {
                const box = document.createElement('input');
                box.type = 'checkbox';
                box.checked = true;
                box.dataset.columns = columnRange(column).join(',');
                cell.appendChild(box);
            }
            row.appendChild(cell);
        });
    });
}

function captureLayout() {
    tradeHubTables().forEach(table => {
        headerRows(table).forEach(row => {
            spannedCells(row).forEach(entry => headerLayout.push(entry));
        });
    });
    // The labels carry the keys, and every table repeats them, so one table
    // answers for all of them.
    const labels = spannedCells(tradeHubTables()[0].tHead.rows[2]);
    labels.filter(column => column.cell.dataset.col).forEach(column => {
        const key = column.cell.dataset.col;
        keyedColumns[key] = (keyedColumns[key] || []).concat(columnRange(column));
    });
}

function applyColumns() {
    headerLayout.forEach(entry => {
        const visible = visibleCount(entry.start, entry.span);
        entry.cell.style.display = visible > 0 ? '' : 'none';
        if (entry.span > 1) {
            // A group keeps a colspan of 1 even when hidden, because
            // colspan="0" means "to the end of the table" in HTML.
            entry.cell.colSpan = Math.max(visible, 1);
        }
    });
    // Body cells carry no colspan, so the column index is the cell index.
    tradeHubTables().forEach(table => {
        for (const row of table.tBodies[0].rows) {
            for (let index = 0; index < row.cells.length; index += 1) {
                row.cells[index].style.display = hiddenColumns.has(index) ? 'none' : '';
            }
        }
    });
}

// The set is the state; the boxes are its view. Both tables therefore agree
// however you got there - a click in one, or a column hidden with its own box.
function syncToggles() {
    $(`.${TOGGLE_ROW_CLASS} input[data-columns]`).each(function () {
        this.checked = !hiddenColumns.has(parseInt(this.dataset.columns, 10));
    });
}

function applyFilters() {
    const maxRecent = parseFloat($('#max-o48').val());
    const minVolume = parseFloat($('#min-hvol-other').val());

    TABLE_IDS.forEach(id => {
        const rows = $(`#${id} tbody tr[data-type-id]`);
        let shown = 0;
        rows.each(function () {
            const row = $(this);
            // Both sides must pass: the number describes a quiet item, not a
            // quiet side.
            const quiet = isNaN(maxRecent)
                || (parseFloat(row.attr('data-o48-sell')) <= maxRecent
                    && parseFloat(row.attr('data-o48-buy')) <= maxRecent);
            // A region with no history reports no volume at all, so the item
            // fails any threshold rather than passing on a missing number.
            const traded = isNaN(minVolume)
                || (parseFloat(row.attr('data-hvol-other')) || 0) >= minVolume;
            row.toggle(quiet && traded);
            if (quiet && traded) {
                shown += 1;
            }
        });
        // Without the count an over-tight filter reads as an empty market.
        $(`.trade-hub-count[data-table="${id}"]`).text(
            rows.length ? `showing ${shown} of ${rows.length}` : '');
    });
}

function syncFilterInputs() {
    Object.keys(FILTER_COLUMNS).forEach(inputId => {
        const columns = keyedColumns[FILTER_COLUMNS[inputId]] || [];
        const live = columns.length > 0 && columns.every(index => !hiddenColumns.has(index));
        const input = $(`#${inputId}`);
        if (!live) {
            input.val('');
        }
        input.prop('disabled', !live);
    });
}

function refresh() {
    syncToggles();
    applyColumns();
    syncFilterInputs();
    applyFilters();
}

$(document).ready(function () {
    if (!document.getElementById('trade-hub-controls')) {
        return;
    }
    buildToggles();
    captureLayout();
    // Browsers restore a typed number on a reload, so the filters run once
    // before anyone touches a control.
    refresh();
    $('#trade-hub-controls').on('input', '.filter-threshold', applyFilters);
    $(TABLE_IDS.map(id => `#${id}`).join(', ')).on(
        'change', `.${TOGGLE_ROW_CLASS} input`, function () {
            const hidden = !this.checked;
            this.dataset.columns.split(',').forEach(index => {
                const column = parseInt(index, 10);
                if (hidden) {
                    hiddenColumns.add(column);
                } else {
                    hiddenColumns.delete(column);
                }
            });
            refresh();
        });
});
