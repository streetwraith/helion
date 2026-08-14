// Shared machinery for the browser notification pollers. Each page supplies its
// endpoint, its cursor and its text; this file owns the toggle, the permission
// handling and the failure policy, so those rules exist once.
//
// A poller runs only while its page is open and its toggle is on. The page
// keeps its own cursor in memory, seeded from a value rendered into the page, so
// a reload never fires for events that happened while the page was closed.
window.helion = window.helion || {};

// Notification bodies quote ISK in the same abbreviated form the tables use.
window.helion.formatIsk = function (value) {
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
};

// Unit prices need their decimals: abbreviating 5.05 ISK to "5" loses the
// entire point of an undercut.
window.helion.formatPrice = function (value) {
    return Number(value).toLocaleString(undefined, {maximumFractionDigits: 2});
};

// config: root, banner, storageKey, tag, params(), handle(data), reset()
// `params` returns the query for each poll. `handle` returns null when the
// response holds nothing new, or {notify: {title, body}, banner: Node}.
window.helion.notifyPoller = function (config) {
    var root = config.root;
    var toggle = root.querySelector('.notify-toggle');
    var status = root.querySelector('.notify-status');
    var banner = config.banner;

    var RETRY_SECONDS = 60;
    var FAILURE_LIMIT = 3;

    // The Notification API needs a secure context. Dev serves plain HTTP, so the
    // poller and the banner still work there and only the OS card is missing.
    var canNotify = window.isSecureContext && 'Notification' in window;

    var timer = null;
    var failures = 0;

    function fireNotification(text) {
        if (!canNotify || Notification.permission !== 'granted') {
            return;
        }
        // One fixed tag per page: the page's counts are cumulative, so a
        // replacement always states the fuller truth, and two open tabs
        // collapse into one card.
        var notification = new Notification(text.title, {body: text.body, tag: config.tag});
        notification.onclick = function () {
            window.focus();
        };
    }

    function showBanner(node) {
        banner.innerHTML = '';
        banner.appendChild(node);
        banner.hidden = false;
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
        $.ajax({url: root.dataset.endpoint, type: 'GET', data: config.params(), dataType: 'json'})
            .done(function (data) {
                failures = 0;
                var result = config.handle(data);
                if (result) {
                    if (result.banner) {
                        showBanner(result.banner);
                    }
                    if (result.notify) {
                        fireNotification(result.notify);
                    }
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
        config.reset();
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
            window.localStorage.setItem(config.storageKey, 'on');
            start(true);
        } else {
            window.localStorage.removeItem(config.storageKey);
            stop('');
            config.reset();
            banner.hidden = true;
        }
    });

    config.reset();
    if (window.localStorage.getItem(config.storageKey) === 'on') {
        toggle.checked = true;
        start(false);
    }
};
