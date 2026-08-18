// Browser notifications for new own transactions. The shared poller in
// notify_poller.js owns the toggle, the permission handling and the failure
// policy; this file owns the cursor and the wording.
$(document).ready(function () {
    var root = document.getElementById('tx-notify');
    if (!root) {
        return;
    }
    var formatIsk = window.helion.formatIsk;

    var cursor = parseInt(root.dataset.cursor, 10) || 0;
    var total = null;

    function reset() {
        total = {count: 0, buys: 0, sells: 0, boughtIsk: 0, soldIsk: 0, latest: null};
    }

    function notificationText() {
        // A single transaction is worth naming; a burst is only worth counting.
        if (total.count === 1 && total.latest) {
            var row = total.latest;
            return {
                title: (row.is_buy ? 'Bought ' : 'Sold ') + row.quantity + 'x '
                    + row.type_name + ' - ' + row.owner,
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

    function bannerNode() {
        var label = total.count === 1 ? '1 new transaction' : total.count + ' new transactions';
        var link = document.createElement('a');
        // The unfiltered list, because the poller ignores the display filters:
        // the rows this banner counted are then provably on page 1.
        link.href = root.dataset.unfilteredUrl;
        link.textContent = label + ' - show all';
        return link;
    }

    window.helion.notifyPoller({
        root: root,
        banner: document.getElementById('tx-notify-banner'),
        storageKey: 'helion.notify.transactions',
        tag: 'helion-transactions',
        reset: reset,
        params: function () {
            return {after: cursor};
        },
        handle: function (data) {
            if (data.count <= 0) {
                return null;
            }
            total.count += data.count;
            total.buys += data.buys;
            total.sells += data.sells;
            total.boughtIsk += data.bought_isk;
            total.soldIsk += data.sold_isk;
            if (data.latest) {
                total.latest = data.latest;
            }
            cursor = data.max_id;
            return {banner: bannerNode(), notify: notificationText()};
        }
    });
});
