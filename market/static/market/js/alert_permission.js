// The permission button on the alerts page.
//
// The alert bar has no toggle, so the browser permission is the only switch on
// the OS card. Firefox and Safari ignore a permission request without a user
// gesture, which is why the request lives behind this button rather than in
// alert_bar.js.
$(document).ready(function () {
    var button = document.getElementById('alert-permission-ask');
    var status = document.getElementById('alert-permission-status');
    if (!button || !status) {
        return;
    }

    // The Notification API needs a secure context. Dev serves plain HTTP, where
    // the bar still works and only the OS card is missing.
    var canNotify = window.isSecureContext && 'Notification' in window;

    function describe() {
        if (!canNotify) {
            status.textContent = 'desktop notifications need HTTPS - the bar still works';
        } else if (Notification.permission === 'granted') {
            status.textContent = 'desktop notifications allowed';
        } else if (Notification.permission === 'denied') {
            status.textContent = 'desktop notifications blocked in the browser - the bar still works';
        } else {
            status.textContent = 'desktop notifications not allowed yet - the bar still works';
        }
        button.hidden = !canNotify || Notification.permission !== 'default';
    }

    button.addEventListener('click', function () {
        Notification.requestPermission().then(describe);
    });

    describe();
});
