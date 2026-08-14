// The blueprint profitability chart: a stack of material costs with the product
// value over it, and the margin below.
//
// The row order below matches get_blueprint_chart in
// market/services/industry.py: x, one running total per material, the material
// total again, the product revenue, the margin percent. Change one and change
// the other. The material rows are cumulative, so each material draws as the
// band between its own row and the row below it.
//
// Both themes are selected, not flipped: the axis, grid and series colours live
// on the canvas, where CSS cannot reach them, so a theme switch rebuilds both
// charts.
//
// Nine categorical fills are more than colour alone can reliably separate. The
// legend and the fixed stack order carry the identification; the palette only
// has to separate each band from the two it touches.
const THEMES = {
    light: {
        bands: ['#0072b2', '#e69f00', '#009e73', '#cc79a7', '#56b4e9',
                '#d55e00', '#b8a02e', '#8c8c8c', '#6a4c93'],
        ink: '#1a1a19',
        axis: '#52514e',
        grid: '#e1e0d9',
        ticks: '#c3c2b7',
        zero: '#898781',
    },
    dark: {
        bands: ['#3b8fd0', '#e8ae33', '#2fb48e', '#d68fb6', '#74c3ee',
                '#e37538', '#c9b44a', '#9a9a9a', '#8b6bb1'],
        ink: '#e8e7e0',
        axis: '#c3c2b7',
        grid: '#2c2c2a',
        ticks: '#383835',
        zero: '#898781',
    },
};

// The two totals are the comparison the page exists to make, so they wear ink
// rather than a tenth hue, and separate from each other by dash and width. The
// material total also traces the top edge of the stack.
const TOTAL_DASH = [6, 4];
const TOTAL_WIDTH_PX = 1;
const PRODUCT_WIDTH_PX = 2;
const MARGIN_WIDTH_PX = 1;

// Multiples of a day, so the date axis never subdivides below one day.
const DAY_INCREMENTS = [1, 2, 7, 14, 30, 60, 90, 180, 365].map(days => days * 86400);
const MARGIN_HEIGHT_PX = 170;
const MAIN_MIN_HEIGHT_PX = 320;
// Two legends and the page footer sit under the canvases.
const CHART_RESERVED_PX = 190;
const CHART_RESIZE_DEBOUNCE_MS = 100;

// Both charts join this group, so a drag-zoom or a cursor move on one follows on
// the other. Only the x scale syncs: the two y scales measure different things.
const SYNC_KEY = 'industry';

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

function percent(value) {
    return value === null || value === undefined ? '-' : value.toFixed(2) + '%';
}

// The x series reads as a plain date. Our x values are UTC midnight, and the
// legend would otherwise print a whole timestamp with a meaningless 00:00:00.
const dateSeries = {
    label: 'date',
    value: (self, timestamp) => timestamp == null
        ? '-'
        : new Date(timestamp * 1000).toISOString().slice(0, 10),
};

// One material's running total. Width 0 draws no line: the material shows as the
// band between this edge and the edge below, and a stroke here would fence every
// band. The stroke colour still has to be set, because the legend swatch reads it.
//
// Only the bottom material takes a `fill`, which reaches the baseline. Every
// other one is filled by its band, and a fill here would spill past the edge
// below it all the way down.
function bandEdgeSeries(label, color, fill) {
    return {
        label: label,
        scale: 'isk',
        stroke: color,
        fill: fill,
        width: 0,
        points: {show: false},
        value: (self, value) => abbreviate(value),
    };
}

function axisStyle(theme) {
    return {
        stroke: theme.axis,
        grid: {stroke: theme.grid, width: 1},
        ticks: {stroke: theme.ticks, width: 1},
    };
}

// uPlot draws no reference lines of its own, and the sign of the margin is the
// whole point of the lower chart.
function drawZeroLine(theme) {
    return (chart) => {
        const y = chart.valToPos(0, 'pct', true);
        if (y < chart.bbox.top || y > chart.bbox.top + chart.bbox.height) {
            return;
        }
        const ctx = chart.ctx;
        ctx.save();
        ctx.strokeStyle = theme.zero;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(chart.bbox.left, y);
        ctx.lineTo(chart.bbox.left + chart.bbox.width, y);
        ctx.stroke();
        ctx.restore();
    };
}

function mainOptions(size, theme, labels) {
    // The last two labels are the material total and the product; the rest name
    // the bands, one palette entry each.
    const materials = labels.slice(0, labels.length - 2);
    return {
        width: size.width,
        height: size.height,
        // EVE's market day is a UTC day, so the viewer's timezone must not shift
        // a point onto the day before or after.
        tzDate: (timestamp) => uPlot.tzDate(new Date(timestamp * 1000), 'Etc/UTC'),
        cursor: {sync: {key: SYNC_KEY, scales: ['x', null]}},
        scales: {
            // A stack has to stand on zero: a clipped baseline would make the
            // bands lie about their share of the cost.
            isk: {range: (self, min, max) => [0, max * 1.03]},
        },
        axes: [
            {...axisStyle(theme), rotate: -45, incrs: DAY_INCREMENTS},
            {...axisStyle(theme), scale: 'isk', side: 3, label: 'ISK per run',
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
            ...materials.map((label, index) => bandEdgeSeries(
                label, theme.bands[index], index === 0 ? theme.bands[0] : null)),
            {
                label: 'materials',
                scale: 'isk',
                stroke: theme.ink,
                width: TOTAL_WIDTH_PX,
                dash: TOTAL_DASH,
                points: {show: false},
                value: (self, value) => abbreviate(value),
            },
            {
                label: 'product',
                scale: 'isk',
                stroke: theme.ink,
                width: PRODUCT_WIDTH_PX,
                points: {show: false},
                value: (self, value) => abbreviate(value),
            },
        ],
        // The first material fills to the baseline; every later one fills down to
        // the material below it. uPlot takes the upper series first.
        bands: materials.slice(1).map((label, index) => ({
            series: [index + 2, index + 1],
            fill: theme.bands[index + 1],
        })),
    };
}

// The margin panel keeps a fixed height: it only has to show the sign and the
// size of the gap, and the space belongs to the stack above it.
function marginOptions(width, theme) {
    return {
        width: width,
        height: MARGIN_HEIGHT_PX,
        tzDate: (timestamp) => uPlot.tzDate(new Date(timestamp * 1000), 'Etc/UTC'),
        cursor: {sync: {key: SYNC_KEY, scales: ['x', null]}},
        axes: [
            {...axisStyle(theme), rotate: -45, incrs: DAY_INCREMENTS},
            {...axisStyle(theme), scale: 'pct', side: 3, label: 'margin %',
             values: (self, splits) => splits.map(split => split.toFixed(0))},
        ],
        legend: {
            markers: {
                width: 0,
                fill: (self, seriesIdx) => self.series[seriesIdx].stroke(self, seriesIdx),
            },
        },
        series: [
            dateSeries,
            {
                label: 'margin',
                scale: 'pct',
                stroke: theme.ink,
                width: MARGIN_WIDTH_PX,
                points: {show: false},
                value: (self, value) => percent(value),
            },
        ],
        hooks: {draw: [drawZeroLine(theme)]},
    };
}

function chartSize(element) {
    const top = element.getBoundingClientRect().top + window.scrollY;
    return {
        width: element.clientWidth,
        height: Math.max(
            MAIN_MIN_HEIGHT_PX,
            window.innerHeight - top - MARGIN_HEIGHT_PX - CHART_RESERVED_PX),
    };
}

function drawCharts() {
    const dataElement = document.getElementById('industry-chart-data');
    if (!dataElement) {
        return;
    }
    const rows = JSON.parse(dataElement.textContent);
    const labels = JSON.parse(document.getElementById('industry-series-labels').textContent);
    const mainTarget = document.getElementById('industry-chart');
    const marginTarget = document.getElementById('industry-margin');
    // The margin is the last row; everything before it belongs to the stack.
    const mainData = rows.slice(0, rows.length - 1);
    const marginData = [rows[0], rows[rows.length - 1]];
    let main = null;
    let margin = null;

    function build() {
        if (main) {
            main.destroy();
            margin.destroy();
        }
        const theme = currentTheme();
        main = new uPlot(mainOptions(chartSize(mainTarget), theme, labels), mainData, mainTarget);
        margin = new uPlot(
            marginOptions(marginTarget.clientWidth, theme), marginData, marginTarget);
    }

    build();

    let resizeTimer = null;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function() {
            main.setSize(chartSize(mainTarget));
            margin.setSize({width: marginTarget.clientWidth, height: MARGIN_HEIGHT_PX});
        }, CHART_RESIZE_DEBOUNCE_MS);
    });
    darkQuery.addEventListener('change', build);
}

$(document).ready(drawCharts);
