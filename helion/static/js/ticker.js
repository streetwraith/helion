// The header ticker sparklines.
//
// peity draws into the element it is called on, so every page that carries
// charts initialises its own. The ice page keeps its own call for its .chart
// cells; this one only takes the header.
$(document).ready(function() {
    $('#price-ticker .ticker-chart').peity('line');
});
