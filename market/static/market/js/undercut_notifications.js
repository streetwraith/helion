// Browser notifications for own orders that lost the top of the book in this
// hub region. A sell order is undercut, a buy order is outbid.
//
// The page is not re-rendered: building it costs seconds, so the affected rows
// are marked instead and their cells stay as they were rendered. The card and
// the banner carry the fresh prices.
$(document).ready(function () {
    var root = document.getElementById('undercut-notify');
    if (!root) {
        return;
    }
    var formatPrice = window.helion.formatPrice;

    // A banner names at most this many items before it gives up and counts.
    var MAX_NAMES = 5;

    var cursor = parseInt(root.dataset.cursor, 10) || 0;
    var total = null;

    function reset() {
        total = {count: 0, undercut: 0, outbid: 0, names: [], latest: null};
    }

    function markRows(items) {
        items.forEach(function (item) {
            // Two tables carry rows: the trade list and the extras below it.
            var rows = document.querySelectorAll('tr[data-type-id="' + item.type_id + '"]');
            Array.prototype.forEach.call(rows, function (row) {
                row.classList.add('notify-new');
            });
        });
    }

    function notificationText() {
        // A single order is worth naming with both prices; a burst is only
        // worth counting per side.
        if (total.count === 1 && total.latest) {
            var item = total.latest;
            return {
                title: item.name + (item.is_buy ? ' outbid' : ' undercut'),
                body: 'yours ' + formatPrice(item.my_price)
                    + ', theirs ' + formatPrice(item.their_price)
            };
        }
        var parts = [];
        if (total.undercut > 0) {
            parts.push(total.undercut + ' undercut');
        }
        if (total.outbid > 0) {
            parts.push(total.outbid + ' outbid');
        }
        var listed = total.names.join(', ');
        if (total.count > total.names.length) {
            listed += ' and ' + (total.count - total.names.length) + ' more';
        }
        return {title: parts.join(', '), body: listed};
    }

    function bannerNode(text) {
        var span = document.createElement('span');
        span.textContent = text.title + ' - ' + text.body;
        return span;
    }

    window.helion.notifyPoller({
        root: root,
        banner: document.getElementById('undercut-notify-banner'),
        storageKey: 'helion.notify.undercuts.' + root.dataset.regionId,
        tag: 'helion-undercuts-' + root.dataset.regionId,
        reset: reset,
        params: function () {
            return {after: cursor};
        },
        handle: function (data) {
            if (data.count <= 0) {
                return null;
            }
            total.count += data.count;
            total.undercut += data.undercut;
            total.outbid += data.outbid;
            data.items.forEach(function (item) {
                if (total.names.length < MAX_NAMES) {
                    total.names.push(item.name);
                }
            });
            total.latest = data.items.length ? data.items[0] : null;
            cursor = data.max_id;
            markRows(data.items);
            var text = notificationText();
            return {banner: bannerNode(text), notify: text};
        }
    });
});
