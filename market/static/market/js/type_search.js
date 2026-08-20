// The item search box, shared by the history chart, the market browser and the
// price alerts page.
//
// A page carries one box, so the ids are fixed. The form is read from the
// input rather than named, because the pages name their forms differently
// and all submit the same hidden type_id.
//
// Picking an item submits the form, because on the two browsing pages the item
// is the whole query. A page whose form still needs other fields sets
// data-submit-on-pick="false" on the box and submits when the user says so.
const SEARCH_MIN_CHARS = 3;
const SEARCH_DEBOUNCE_MS = 200;

function bindTypeSearch() {
    const box = $('#type-search-box');
    const input = $('#type_search');
    const results = $('#type-search-results');
    const form = input.closest('form');
    const submitOnPick = box.data('submit-on-pick') !== false;
    let searchTimer = null;
    let request = null;

    function hide() {
        results.empty().hide();
    }

    function choose(typeId, name) {
        $('#type_id').val(typeId);
        if (submitOnPick) {
            form[0].submit();
            return;
        }
        // No navigation follows, so the box has to show the choice itself.
        input.val(name);
        hide();
    }

    function render(matches) {
        results.empty();
        matches.forEach(function(match) {
            $('<li>').text(match.name).attr('data-type-id', match.type_id).appendTo(results);
        });
        results.toggle(matches.length > 0);
    }

    input.on('input', function() {
        const query = input.val().trim();
        clearTimeout(searchTimer);
        if (query.length < SEARCH_MIN_CHARS) {
            hide();
            return;
        }
        searchTimer = setTimeout(function() {
            if (request) {
                request.abort();
            }
            request = $.ajax({
                url: '/market/ajax/type_search',
                data: {q: query},
                dataType: 'json',
                success: render,
                error: hide
            });
        }, SEARCH_DEBOUNCE_MS);
    });

    input.on('keydown', function(event) {
        const items = results.children('li');
        if (event.key === 'Escape') {
            hide();
            return;
        }
        if (event.key === 'Enter') {
            // Always swallow Enter: a native submit would send the type id of the
            // item shown before this search, under the name just typed.
            event.preventDefault();
            const active = items.filter('.active');
            const chosen = active.length ? active : items.first();
            if (chosen.length) {
                choose(chosen.data('type-id'), chosen.text());
            }
            return;
        }
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') {
            return;
        }
        event.preventDefault();
        if (items.length === 0) {
            return;
        }
        const step = event.key === 'ArrowDown' ? 1 : -1;
        let index = items.index(items.filter('.active')) + step;
        if (index < 0) {
            index = items.length - 1;
        }
        if (index >= items.length) {
            index = 0;
        }
        items.removeClass('active').eq(index).addClass('active');
    });

    results.on('click', 'li', function() {
        choose($(this).data('type-id'), $(this).text());
    });

    $(document).on('click', function(event) {
        if ($(event.target).closest('#type-search-box').length === 0) {
            hide();
        }
    });
}

$(document).ready(bindTypeSearch);
