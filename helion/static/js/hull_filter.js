// The hull datasheets page: the name filter and the stat toggles. Which stats
// you keep is per browser, so it survives a reload without a server round trip.
const HULL_STORAGE_KEY = 'helion.hulls.hiddenStats';

function readHiddenStats() {
    try {
        return new Set(JSON.parse(localStorage.getItem(HULL_STORAGE_KEY) || '[]'));
    } catch (error) {
        // A private window or cleared site data: fall back to showing everything.
        return new Set();
    }
}

function writeHiddenStats(hidden) {
    try {
        localStorage.setItem(HULL_STORAGE_KEY, JSON.stringify([...hidden]));
    } catch (error) {
        // Nothing to recover: the page still works, it just forgets.
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const field = document.getElementById('hull-filter');
    const cards = [...document.querySelectorAll('.hull-card')];
    const boxes = [...document.querySelectorAll('.hull-class, .hull-tier, .hull-size')];
    const checkboxes = [...document.querySelectorAll('#hull-toggles input[data-stat]')];
    if (!field) {
        return;
    }

    // One rule per stat key beats touching every row: 400 cards carry thousands.
    const style = document.createElement('style');
    document.head.append(style);
    const hidden = readHiddenStats();

    function applyStats() {
        const rules = [...hidden].map((key) => `.stat[data-stat="${key}"]{display:none}`);
        style.textContent = rules.join('');
        writeHiddenStats(hidden);
    }

    for (const checkbox of checkboxes) {
        checkbox.checked = !hidden.has(checkbox.dataset.stat);
        checkbox.addEventListener('change', () => {
            if (checkbox.checked) {
                hidden.delete(checkbox.dataset.stat);
            } else {
                hidden.add(checkbox.dataset.stat);
            }
            applyStats();
        });
    }

    function setAll(checked) {
        for (const checkbox of checkboxes) {
            checkbox.checked = checked;
            if (checked) {
                hidden.delete(checkbox.dataset.stat);
            } else {
                hidden.add(checkbox.dataset.stat);
            }
        }
        applyStats();
    }

    document.getElementById('hull-toggle-all').addEventListener('click', () => setAll(true));
    document.getElementById('hull-toggle-none').addEventListener('click', () => setAll(false));
    applyStats();

    // The name and the trait text, read once: a card carries hundreds of
    // characters of bonus text, and re-reading it on every keystroke would walk
    // the whole page again. The figures stay out of it, so a term like "50"
    // matches a bonus rather than every capacitor on the page.
    const haystacks = new Map(cards.map((card) => {
        const name = card.dataset.name;
        const traits = card.querySelector('.hull-traits');
        return [card, (name + ' ' + (traits ? traits.textContent : '')).toLowerCase()];
    }));

    // A strategic cruiser keeps its subsystem bonuses in closed blocks, so a
    // match on that text would show a card with nothing to read. The filter
    // opens such a block, and closes again only the blocks it opened itself.
    const blocks = new Map([...document.querySelectorAll('.trait-subsystems')]
        .map((block) => [block, block.textContent.toLowerCase()]));
    const opened = new Set();

    function applyBlocks(term) {
        for (const [block, haystack] of blocks) {
            if (term && haystack.includes(term)) {
                if (!block.open) {
                    block.open = true;
                    opened.add(block);
                }
            } else if (opened.delete(block)) {
                block.open = false;
            }
        }
    }

    field.addEventListener('input', () => {
        const term = field.value.trim().toLowerCase();
        applyBlocks(term);
        for (const card of cards) {
            card.classList.toggle('hull-hidden', term && !haystacks.get(card).includes(term));
        }
        // A heading whose cards all went hides with them.
        for (const box of boxes) {
            box.classList.toggle('hull-hidden',
                                 box.querySelectorAll('.hull-card:not(.hull-hidden)').length === 0);
        }
    });
});
