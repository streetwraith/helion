// Browser notifications for new station-trading mistakes in this hub region.
// The poller replaces the table body whenever marketmanager refreshes the
// region, because mistakes clear as well as appear, and fires a card only for a
// mistake it has not seen before whose profit clears the threshold.
$(document).ready(function () {
    var root = document.getElementById('mistake-notify');
    if (!root) {
        return;
    }
    var tbody = document.querySelector('#mistakes-table tbody');
    var thresholdInput = root.querySelector('.notify-threshold');
    var formatIsk = window.helion.formatIsk;

    // A banner names at most this many items before it gives up and counts.
    var MAX_NAMES = 5;

    // Keyed by region: the hubs differ by more than two orders of magnitude in
    // profit, so one threshold cannot suit them all.
    var STORAGE_KEY = 'helion.notify.mistakes.' + root.dataset.regionId;
    var THRESHOLD_KEY = STORAGE_KEY + '.threshold';

    var stamp = root.dataset.refreshedAt;
    // Every order id the poller has observed, whether or not it fired a card.
    // Recording all of them is what stops a lowered threshold from reporting a
    // backlog of mistakes that were already on screen.
    var seen = {};

    function rows() {
        return tbody ? Array.prototype.slice.call(tbody.querySelectorAll('tr[data-order-id]')) : [];
    }

    // Records every rendered row and returns only the ones that are new.
    function observe() {
        var added = [];
        rows().forEach(function (row) {
            var orderId = row.dataset.orderId;
            if (!seen[orderId]) {
                seen[orderId] = true;
                added.push(row);
            }
        });
        return added;
    }

    function threshold() {
        // An empty or unreadable box means no floor at all.
        var millions = parseFloat(thresholdInput.value);
        return isFinite(millions) && millions > 0 ? millions * 1e6 : 0;
    }

    function reset() {
        seen = {};
        // Seed from what is already on screen, so turning the toggle on reports
        // what happens next rather than what is already in front of you.
        observe();
    }

    function describe(worthy) {
        var names = worthy.map(function (row) {
            return row.dataset.itemName;
        });
        var listed = names.slice(0, MAX_NAMES).join(', ');
        if (names.length > MAX_NAMES) {
            listed += ' and ' + (names.length - MAX_NAMES) + ' more';
        }
        var best = Math.max.apply(null, worthy.map(function (row) {
            return parseFloat(row.dataset.profit);
        }));
        if (worthy.length === 1) {
            return {
                title: names[0] + ' mistake',
                body: formatIsk(best) + ' ISK profit'
            };
        }
        return {
            title: worthy.length + ' new mistakes',
            body: 'best ' + formatIsk(best) + ' ISK - ' + listed
        };
    }

    function bannerNode(text) {
        var span = document.createElement('span');
        span.textContent = text.title + ' - ' + text.body;
        return span;
    }

    thresholdInput.value = window.localStorage.getItem(THRESHOLD_KEY) || '';
    thresholdInput.addEventListener('change', function () {
        window.localStorage.setItem(THRESHOLD_KEY, thresholdInput.value);
    });

    window.helion.notifyPoller({
        root: root,
        banner: document.getElementById('mistake-notify-banner'),
        storageKey: STORAGE_KEY,
        tag: 'helion-mistakes-' + root.dataset.regionId,
        reset: reset,
        params: function () {
            return {seen: stamp};
        },
        handle: function (data) {
            if (!data.changed) {
                return null;
            }
            stamp = data.refreshed_at;
            if (tbody) {
                tbody.innerHTML = data.html;
            }
            var floor = threshold();
            var worthy = observe().filter(function (row) {
                var profit = parseFloat(row.dataset.profit);
                // A mistake with no second-best sell has no exit in this
                // station, so its profit is zero and it can never be acted on.
                return profit > 0 && profit >= floor;
            });
            // A swap with nothing new above the floor stays silent on purpose:
            // the table still had to be replaced, because mistakes clear too.
            if (worthy.length === 0) {
                return null;
            }
            worthy.forEach(function (row) {
                row.classList.add('notify-new');
            });
            var text = describe(worthy);
            return {banner: bannerNode(text), notify: text};
        }
    });
});
