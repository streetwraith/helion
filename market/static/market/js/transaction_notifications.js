// Browser notifications for new own transactions. The poller runs only while
// this page is open and the toggle is on. The cursor lives in memory and starts
// at the value rendered into the page, so a reload never fires for transactions
// that arrived while the page was closed.
$(document).ready(function () {
    var root = document.getElementById('tx-notify');
    if (!root) {
        return;
    }
    var toggle = document.getElementById('tx-notify-toggle');
    var status = document.getElementById('tx-notify-status');
    var banner = document.getElementById('tx-notify-banner');

    var STORAGE_KEY = 'helion.notify.transactions';
    var NOTIFICATION_TAG = 'helion-transactions';
    var RETRY_SECONDS = 60;
    var FAILURE_LIMIT = 3;

    // The Notification API needs a secure context. Dev serves plain HTTP, so the
    // poller and the banner still work there and only the OS card is missing.
    var canNotify = window.isSecureContext && 'Notification' in window;

    var cursor = parseInt(root.dataset.cursor, 10) || 0;
    var timer = null;
    var failures = 0;
    var total = null;

    function reset() {
        total = {count: 0, buys: 0, sells: 0, boughtIsk: 0, soldIsk: 0, latest: null};
    }

    function formatIsk(value) {
        if (value >= 1e9) {
            return (value / 1e9).toFixed(2) + 'B';
        }
        if (value >= 1e6) {
            return (value / 1e6).toFixed(1) + 'M';
        }
        if (value >= 1e3) {
            return (value / 1e3).toFixed(1) + 'K';
        }
        return value.toFixed(0);
    }

    function notificationText() {
        // A single transaction is worth naming; a burst is only worth counting.
        if (total.count === 1 && total.latest) {
            var row = total.latest;
            return {
                title: (row.is_buy ? 'Bought ' : 'Sold ') + row.quantity + 'x ' + row.type_name,
                body: formatIsk(row.isk) + ' ISK - ' + row.location
            };
        }
        // Two unsigned figures, never a signed net: a restock and a payday must
        // not be able to look like each other.
        return {
            title: total.count + ' new transactions',
            body: total.buys + ' buys, ' + total.sells + ' sells - sold '
                + formatIsk(total.soldIsk) + ', bought ' + formatIsk(total.boughtIsk) + ' ISK'
        };
    }

    function showBanner() {
        var label = total.count === 1 ? '1 new transaction' : total.count + ' new transactions';
        var link = document.createElement('a');
        // The unfiltered list, because the poller ignores the display filters:
        // the rows this banner counted are then provably on page 1.
        link.href = root.dataset.unfilteredUrl;
        link.textContent = label + ' - show all';
        banner.innerHTML = '';
        banner.appendChild(link);
        banner.hidden = false;
    }

    function fireNotification() {
        if (!canNotify || Notification.permission !== 'granted') {
            return;
        }
        var text = notificationText();
        // One fixed tag: the count is cumulative, so a replacement always states
        // the fuller truth, and two open tabs collapse into one card.
        var notification = new Notification(text.title, {body: text.body, tag: NOTIFICATION_TAG});
        notification.onclick = function () {
            window.focus();
        };
    }

    function accumulate(data) {
        total.count += data.count;
        total.buys += data.buys;
        total.sells += data.sells;
        total.boughtIsk += data.bought_isk;
        total.soldIsk += data.sold_isk;
        if (data.latest) {
            total.latest = data.latest;
        }
    }

    function schedule(seconds) {
        timer = window.setTimeout(poll, seconds * 1000);
    }

    function stop(note) {
        window.clearTimeout(timer);
        timer = null;
        status.textContent = note;
    }

    function poll() {
        $.ajax({url: root.dataset.endpoint, type: 'GET', data: {after: cursor}, dataType: 'json'})
            .done(function (data) {
                failures = 0;
                if (data.count > 0) {
                    accumulate(data);
                    cursor = data.max_id;
                    showBanner();
                    fireNotification();
                }
                schedule(data.next_poll_seconds);
            })
            .fail(function () {
                // An expired session redirects to an HTML page, which fails the
                // json parse. Three failures in a row and the poller says so
                // rather than looking alive forever.
                failures += 1;
                if (failures >= FAILURE_LIMIT) {
                    stop('notifications stopped - reload the page');
                } else {
                    schedule(RETRY_SECONDS);
                }
            });
    }

    function describePermission() {
        if (!canNotify) {
            status.textContent = 'OS notifications need HTTPS - banner only';
        } else if (Notification.permission === 'granted') {
            status.textContent = '';
        } else if (Notification.permission === 'denied') {
            status.textContent = 'notification permission denied - banner only';
        } else {
            status.textContent = 'notification permission not granted - banner only';
        }
    }

    function start(askPermission) {
        failures = 0;
        reset();
        // Firefox and Safari ignore a permission request without a user gesture,
        // so only the click asks. A restored toggle reports the state instead.
        if (askPermission && canNotify && Notification.permission === 'default') {
            Notification.requestPermission().then(describePermission);
        } else {
            describePermission();
        }
        poll();
    }

    toggle.addEventListener('change', function () {
        if (toggle.checked) {
            window.localStorage.setItem(STORAGE_KEY, 'on');
            start(true);
        } else {
            window.localStorage.removeItem(STORAGE_KEY);
            stop('');
            reset();
            banner.hidden = true;
        }
    });

    reset();
    if (window.localStorage.getItem(STORAGE_KEY) === 'on') {
        toggle.checked = true;
        start(false);
    }
});
