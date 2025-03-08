function openDialog(event, content) {
    let dialog = $("#dialog");
    $("#dialog-content").html(content);

    // Get viewport dimensions
    let windowWidth = $(window).width();
    let windowHeight = $(window).height();

    // Get scroll offsets
    let scrollLeft = $(window).scrollLeft();
    let scrollTop = $(window).scrollTop();

    // Get dialog dimensions
    let dialogWidth = dialog.outerWidth();
    let dialogHeight = dialog.outerHeight();

    // Default position (accounting for scroll)
    let x = event.pageX - scrollLeft;
    let y = event.pageY - scrollTop;

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

    // Apply adjusted position, considering scroll offsets
    dialog.css({
    left: x + scrollLeft + "px",
    top: y + scrollTop + "px"
    }).fadeIn(200); // Show with animation

    // Prevent clicks inside the dialog from triggering a reposition
    dialog.off("click").click(function(event) {
    event.stopPropagation();
    });
}

function closeDialog(event) {
    $("#dialog").fadeOut(200);
}

$(document).ready(function() {
    $('#closeDialog').on('click', function(event) {
        closeDialog(event);
    });
});
