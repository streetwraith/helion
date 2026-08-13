// The market history chart and its item search box.
//
// The series order below matches get_market_history_chart in
// market/services/history.py: volume, low, high, average, 5d average, 30d
// average. Change one and change the other.
//
// Both themes are selected, not flipped: each one has its own steps, validated
// against its own surface. The axis, grid and series colours live on the canvas,
// so CSS cannot reach them: a theme switch has to rebuild the chart.
//
// The two moving averages are two steps of one blue ramp, because they are one
// measure at two smoothing lengths - an ordinal pair, which one hue light-to-dark
// encodes correctly. The steps are far enough apart to pass the ordinal checks;
// two *adjacent* steps do not (normal-vision delta E 9.9 against a floor of 15),
// and both lines are solid, so nothing else would carry the difference.
//
// The raw daily average therefore wears neutral ink, not blue: it would otherwise
// be the same colour as the 5-day line that smooths it. Grey for observed, blue
// for derived.
const THEMES = {
    light: {
        observed: '#52514e',
        bandEdge: '#2a78d6',
        band: 'rgba(42, 120, 214, 0.14)',
        maShort: '#2a78d6',
        maLong: '#86b6ef',
        volume: '#c3c2b7',
        axis: '#52514e',
        grid: '#e1e0d9',
        ticks: '#c3c2b7',
        buy: '#e34948',
        sell: '#008300',
        buyFaded: 'rgba(227, 73, 72, 0.45)',
        sellFaded: 'rgba(0, 131, 0, 0.45)',
    },
    dark: {
        observed: '#c3c2b7',
        bandEdge: '#3987e5',
        band: 'rgba(57, 135, 229, 0.20)',
        maShort: '#3987e5',
        maLong: '#9ec5f4',
        volume: '#52514e',
        axis: '#c3c2b7',
        grid: '#2c2c2a',
        ticks: '#383835',
        buy: '#e66767',
        sell: '#008300',
        buyFaded: 'rgba(230, 103, 103, 0.45)',
        sellFaded: 'rgba(0, 131, 0, 0.55)',
    },
};

// Diameter in CSS pixels. A year of daily points leaves about 5px per day, so
// the dots have to stay under that to read as separate marks.
const AVERAGE_DOT_PX = 3;
const AVERAGE_LINE_PX = 1;
// Our own fills are sparse events, not a daily series, so they can be big enough
// to read at a glance. The cross-region ones are smaller and faded: same event,
// different market.
const FILL_DOT_PX = 7;
const FILL_DOT_OTHER_PX = 5;

// Multiples of a day, so the date axis never subdivides below one day.
const DAY_INCREMENTS = [1, 2, 7, 14, 30, 60, 90, 180, 365].map(days => days * 86400);
// The bars stay inside this share of the plot height, so they never fight the
// price marks above them.
const VOLUME_PLOT_SHARE = 0.3;
// uPlot renders its legend as a separate element under the canvas, so the height
// of the canvas has to leave room for the legend and the page footer.
const CHART_RESERVED_PX = 90;
const CHART_MIN_HEIGHT_PX = 300;
const CHART_RESIZE_DEBOUNCE_MS = 100;

const SEARCH_MIN_CHARS = 3;
const SEARCH_DEBOUNCE_MS = 200;

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

function currentTheme() {
    return darkQuery.matches ? THEMES.dark : THEMES.light;
}

function abbreviate(value) {
    if (value === null || value === undefined) {
        return '-';
    }
    const size = Math.abs(value);
    if (size >= 1e9) {
        return (value / 1e9).toFixed(2) + 'b';
    }
    if (size >= 1e6) {
        return (value / 1e6).toFixed(2) + 'm';
    }
    if (size >= 1e3) {
        return (value / 1e3).toFixed(1) + 'k';
    }
    return value.toFixed(0);
}

// The two edges of the daily range. Width 0 draws no line, and uPlot builds the
// fill path anyway because the upper edge belongs to a band - so the range shows
// as one wash instead of 355 dots per edge. Never give these a fill: only the
// upper edge takes the band branch, so a fill on the lower edge would spill down
// to the baseline.
function bandEdgeSeries(label, theme) {
    return {
        label: label,
        scale: 'isk',
        stroke: theme.bandEdge,
        width: 0,
        points: {show: false},
        value: (self, value) => abbreviate(value),
    };
}

function averageSeries(label, color) {
    return {
        label: label,
        scale: 'isk',
        stroke: color,
        width: AVERAGE_LINE_PX,
        points: {show: false},
        value: (self, value) => abbreviate(value),
    };
}

// One of our own fills: a dot on the price scale, drawn over everything else.
function fillSeries(label, color, size) {
    return {
        label: label,
        scale: 'isk',
        stroke: color,
        paths: () => null,
        points: {show: true, size: size, stroke: color, fill: color},
        value: (self, value) => abbreviate(value),
    };
}

// Red for buys and green for sells, matching the transaction tables elsewhere in
// the app. That pair sits at CVD delta E 7.2 on the light surface, inside the
// 6-to-8 band that needs a second channel: the legend labels supply it. Do not
// also encode buy and sell by dot size - size already separates this region from
// the others.
function transactionSeries(theme) {
    return [
        fillSeries('buy', theme.buy, FILL_DOT_PX),
        fillSeries('buy x', theme.buyFaded, FILL_DOT_OTHER_PX),
        fillSeries('sell', theme.sell, FILL_DOT_PX),
        fillSeries('sell x', theme.sellFaded, FILL_DOT_OTHER_PX),
    ];
}

// The x series reads as a plain date. Our x values are UTC midnight, and the
// legend would otherwise print a whole timestamp with a meaningless 00:00:00.
const dateSeries = {
    label: 'date',
    value: (self, timestamp) => timestamp == null
        ? '-'
        : new Date(timestamp * 1000).toISOString().slice(0, 10),
};

function chartOptions(size, theme, withTransactions) {
    const axisStyle = {
        stroke: theme.axis,
        grid: {stroke: theme.grid, width: 1},
        ticks: {stroke: theme.ticks, width: 1},
    };
    return {
        width: size.width,
        height: size.height,
        // EVE's market day is a UTC day, so the viewer's timezone must not shift
        // a point onto the day before or after.
        tzDate: (timestamp) => uPlot.tzDate(new Date(timestamp * 1000), 'Etc/UTC'),
        scales: {
            vol: {
                range: (self, min, max) => [0, max > 0 ? max / VOLUME_PLOT_SHARE : 1],
            },
        },
        axes: [
            {...axisStyle, rotate: -45, incrs: DAY_INCREMENTS},
            {...axisStyle, scale: 'isk', side: 3, label: 'price',
             values: (self, splits) => splits.map(abbreviate)},
            // One grid is enough; a second set of lines only adds noise.
            {...axisStyle, scale: 'vol', side: 1, label: 'volume', grid: {show: false},
             values: (self, splits) => splits.map(abbreviate)},
        ],
        // The swatch is a solid block of the series colour with no border of its
        // own. Losing the border means the swatch has to be filled, or a series
        // whose fill is null would leave an invisible marker.
        legend: {
            markers: {
                width: 0,
                fill: (self, seriesIdx) => self.series[seriesIdx].stroke(self, seriesIdx),
            },
        },
        series: [
            dateSeries,
            {
                label: 'vol',
                scale: 'vol',
                stroke: theme.volume,
                fill: theme.volume,
                paths: uPlot.paths.bars({size: [0.6, 20]}),
                points: {show: false},
                value: (self, value) => abbreviate(value),
            },
            bandEdgeSeries('l', theme),
            bandEdgeSeries('h', theme),
            {
                label: 'avg',
                scale: 'isk',
                stroke: theme.observed,
                paths: () => null,
                points: {show: true, size: AVERAGE_DOT_PX,
                         stroke: theme.observed, fill: theme.observed},
                value: (self, value) => abbreviate(value),
            },
            averageSeries('mavg5', theme.maShort),
            averageSeries('mavg30', theme.maLong),
            // Last, so our own fills draw over the history behind them.
            ...(withTransactions ? transactionSeries(theme) : []),
        ],
        // Series 3 is the high and series 2 the low: uPlot fills between the
        // upper and the lower edge, in that order.
        bands: [{series: [3, 2], fill: theme.band}],
    };
}

function chartSize(element) {
    const top = element.getBoundingClientRect().top + window.scrollY;
    return {
        width: element.clientWidth,
        height: Math.max(CHART_MIN_HEIGHT_PX, window.innerHeight - top - CHART_RESERVED_PX),
    };
}

function drawChart() {
    const dataElement = document.getElementById('chart-data');
    if (!dataElement) {
        return;
    }
    const target = document.getElementById('history-chart');
    const data = JSON.parse(dataElement.textContent);
    // The view decides whether our own fills are in the payload, so the series
    // list is never guessed from the row count.
    const withTransactions = target.dataset.transactions === '1';
    let chart = null;

    function build() {
        if (chart) {
            chart.destroy();
        }
        chart = new uPlot(
            chartOptions(chartSize(target), currentTheme(), withTransactions), data, target);
    }

    build();

    let resizeTimer = null;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => chart.setSize(chartSize(target)), CHART_RESIZE_DEBOUNCE_MS);
    });
    darkQuery.addEventListener('change', build);
}

function bindTypeSearch() {
    const input = $('#type_search');
    const results = $('#type-search-results');
    const form = $('#history-controls');
    let searchTimer = null;
    let request = null;

    function hide() {
        results.empty().hide();
    }

    function choose(typeId) {
        $('#type_id').val(typeId);
        form[0].submit();
    }

    function render(matches) {
        results.empty();
        matches.forEach(function(match) {
            $('<li>').text(match.name).attr('data-type-id', match.type_id).appendTo(results);
        });
        results.toggle(matches.length > 0);
    }

    input.on('input', function() {
        const query = input.val().trim();
        clearTimeout(searchTimer);
        if (query.length < SEARCH_MIN_CHARS) {
            hide();
            return;
        }
        searchTimer = setTimeout(function() {
            if (request) {
                request.abort();
            }
            request = $.ajax({
                url: '/market/ajax/type_search',
                data: {q: query},
                dataType: 'json',
                success: render,
                error: hide
            });
        }, SEARCH_DEBOUNCE_MS);
    });

    input.on('keydown', function(event) {
        const items = results.children('li');
        if (event.key === 'Escape') {
            hide();
            return;
        }
        if (event.key === 'Enter') {
            // Always swallow Enter: a native submit would send the type id of the
            // item shown before this search, under the name just typed.
            event.preventDefault();
            const active = items.filter('.active');
            const chosen = active.length ? active : items.first();
            if (chosen.length) {
                choose(chosen.data('type-id'));
            }
            return;
        }
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') {
            return;
        }
        event.preventDefault();
        if (items.length === 0) {
            return;
        }
        const step = event.key === 'ArrowDown' ? 1 : -1;
        let index = items.index(items.filter('.active')) + step;
        if (index < 0) {
            index = items.length - 1;
        }
        if (index >= items.length) {
            index = 0;
        }
        items.removeClass('active').eq(index).addClass('active');
    });

    results.on('click', 'li', function() {
        choose($(this).data('type-id'));
    });

    $(document).on('click', function(event) {
        if ($(event.target).closest('#type-search-box').length === 0) {
            hide();
        }
    });
}

$(document).ready(function() {
    $('#region_id').on('change', function() {
        $('#history-controls')[0].submit();
    });
    bindTypeSearch();
    drawChart();
});
