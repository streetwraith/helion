// The price alert bar, on every page.
//
// The bar shows the live condition rather than a log of fires, so the server
// renders it and this file only swaps it in and raises the OS card. There is no
// toggle: polling has to run for the bar to stay true, so the browser
// permission is the only switch, and the alerts page carries the button for it.
//
// The seen set is rebuilt from the bar after every swap. A key that stays
// present raises nothing, which is what keeps a standing trigger quiet while you
// navigate; a key that leaves and comes back carries a new fired_at and counts
// as a new crossing. A crossing while no tab is open therefore raises no card,
// and the bar still shows it.
$(document).ready(function () {
    var root = document.getElementById('alert-bar');
    if (!root) {
        return;
    }

    // The page render is already current, so the first poll waits a full
    // interval. Every response after that names its own.
    var FIRST_POLL_SECONDS = 60;
    var RETRY_SECONDS = 60;
    var FAILURE_LIMIT = 3;
    // One fixed tag, so two open tabs collapse into one card.
    var TAG = 'helion-price-alerts';

    // The Notification API needs a secure context. Dev serves plain HTTP, so the
    // bar still works there and only the OS card is missing.
    var canNotify = window.isSecureContext && 'Notification' in window;

    var timer = null;
    var failures = 0;
    var seen = keysInBar();

    function keysInBar() {
        var keys = {};
        root.querySelectorAll('.alert-row').forEach(function (row) {
            keys[row.dataset.alertId + ':' + row.dataset.firedAt] = row.dataset.card;
        });
        return keys;
    }

    function fireCard(cards) {
        if (!canNotify || Notification.permission !== 'granted' || cards.length === 0) {
            return;
        }
        // A single crossing names the item; a burst is counted, because a card
        // body is plain text and a list of five reads as noise.
        var body = cards.length === 1 ? cards[0] : cards.length + ' alerts triggered';
        var card = new Notification('Price alert', {body: body, tag: TAG});
        card.onclick = function () {
            window.focus();
        };
    }

    function stop(note) {
        window.clearTimeout(timer);
        timer = null;
        // Say so in the bar itself. Nothing else on the page claims the poller
        // is alive, so a dead loop would otherwise be invisible. The rows stay:
        // they were true when they were drawn, and they are still worth reading.
        var row = document.createElement('div');
        row.className = 'alert-row alert-stopped';
        row.textContent = note;
        root.appendChild(row);
        root.hidden = false;
    }

    function poll() {
        $.ajax({url: root.dataset.endpoint, type: 'GET', dataType: 'json'})
            .done(function (data) {
                failures = 0;
                root.innerHTML = data.html;
                var current = keysInBar();
                root.hidden = Object.keys(current).length === 0;
                var cards = Object.keys(current)
                    .filter(function (key) { return !(key in seen); })
                    .map(function (key) { return current[key]; });
                seen = current;
                fireCard(cards);
                timer = window.setTimeout(poll, data.next_poll_seconds * 1000);
            })
            .fail(function () {
                // An expired session redirects to an HTML page, which fails the
                // json parse. Three failures in a row and the bar says so.
                failures += 1;
                if (failures >= FAILURE_LIMIT) {
                    stop('price alerts stopped - reload the page');
                } else {
                    timer = window.setTimeout(poll, RETRY_SECONDS * 1000);
                }
            });
    }

    timer = window.setTimeout(poll, FIRST_POLL_SECONDS * 1000);
});
