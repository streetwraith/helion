$(document).ready(function() {
    $(document).click(function(event) {
        openDialog(event);
    });

    // Close dialog when clicking the close button
    $("#closeDialog").click(function(event) {
        closeDialog(event);
    });
});

function openDialog(event) {
    let dialog = $("#dialog");

    // Get viewport dimensions
    let windowWidth = $(window).width();
    let windowHeight = $(window).height();

    // Get dialog dimensions
    let dialogWidth = dialog.outerWidth();
    let dialogHeight = dialog.outerHeight();

    // Default position
    let x = event.pageX;
    let y = event.pageY;

    // Prevent overflow on the right side
    if (x + dialogWidth > windowWidth) {
        x = windowWidth - dialogWidth - 10; // Adjust with a small margin
    }

    // Prevent overflow on the bottom
    if (y + dialogHeight > windowHeight) {
        y = windowHeight - dialogHeight - 10;
    }

    // Prevent positioning too close to the top-left corner
    x = Math.max(10, x);
    y = Math.max(10, y);

    // Apply adjusted position
    dialog.css({
        left: x + "px",
        top: y + "px"
    }).fadeIn(200); // Show with animation

    dialog.css({
        left: x + "px",
        top: y + "px"
    }).fadeIn(200); // Show with animation

    // Prevent clicks inside the dialog from triggering a reposition
    dialog.off("click").click(function(event) {
        event.stopPropagation();
    });
}

function closeDialog(event) {
    $("#dialog").fadeOut(200);
}