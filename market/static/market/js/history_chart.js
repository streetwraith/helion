// The market history chart. The item search box beside it is shared with the
// market browser and lives in type_search.js.
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
// for derived. The daily range is observed too, so it takes a faded step of the
// same grey: faded, because the solid average dot sits on top of it.
const THEMES = {
    light: {
        observed: '#52514e',
        range: 'rgba(82, 81, 78, 0.55)',
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
    // buy and sell hold a 12 L* gap, which is what separates them without hue for
    // red-green deficiency. Raising the green to gain contrast closes that gap to
    // under 1 L*, so it stays as it is: at 3.7:1 it already clears the 3:1 floor
    // that applies to a mark rather than to text.
    dark: {
        observed: '#b8bcbe',
        range: 'rgba(184, 188, 190, 0.55)',
        maShort: '#3788f7',
        maLong: '#9ec5f4',
        volume: '#4d5457',
        axis: '#b8bcbe',
        grid: '#262d30',
        ticks: '#333d42',
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
// The range bar stays thinner than the average dot, so the dot reads on top of it.
const RANGE_BAR_PX = 1;
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

// One vertical line per day, from the low to the high, like the wick of a candle.
// uPlot ships no renderer for this, so the path is built by hand in canvas pixels.
// The high series draws it and the low series only carries data, so hiding either
// legend entry hides the whole mark: half a range is not worth drawing.
function rangeBarPaths(lowIdx) {
    return (u, seriesIdx, idx0, idx1) => {
        if (!u.series[lowIdx].show) {
            return null;
        }
        const dates = u.data[0];
        const lows = u.data[lowIdx];
        const highs = u.data[seriesIdx];
        const stroke = new Path2D();
        for (let i = idx0; i <= idx1; i++) {
            // A gap-filled day carries no price on either end.
            if (lows[i] == null || highs[i] == null) {
                continue;
            }
            const x = Math.round(u.valToPos(dates[i], 'x', true));
            stroke.moveTo(x, u.valToPos(highs[i], 'isk', true));
            stroke.lineTo(x, u.valToPos(lows[i], 'isk', true));
        }
        return {stroke: stroke, fill: null};
    };
}

// The low end of the range. It draws nothing itself; the legend reads its value.
function rangeLowSeries(label, theme) {
    return {
        label: label,
        scale: 'isk',
        stroke: theme.range,
        paths: () => null,
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

function chartOptions(size, theme) {
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
            rangeLowSeries('l', theme),
            {
                label: 'h',
                scale: 'isk',
                stroke: theme.range,
                width: RANGE_BAR_PX,
                // 2 is the low row; the series order sits at the top of this file.
                paths: rangeBarPaths(2),
                points: {show: false},
                value: (self, value) => abbreviate(value),
            },
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
            ...transactionSeries(theme),
        ],
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
    let chart = null;

    function build() {
        if (chart) {
            chart.destroy();
        }
        chart = new uPlot(
            chartOptions(chartSize(target), currentTheme()), data, target);
    }

    build();

    let resizeTimer = null;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => chart.setSize(chartSize(target)), CHART_RESIZE_DEBOUNCE_MS);
    });
    darkQuery.addEventListener('change', build);
}

$(document).ready(function() {
    $('#region_id').on('change', function() {
        $('#history-controls')[0].submit();
    });
    drawChart();
});
