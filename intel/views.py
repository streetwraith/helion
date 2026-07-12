from django.http import Http404
from django.shortcuts import render

from .mock_data import DEFAULT_WINDOW, WINDOW_BTN, WINDOW_LABELS, WINDOWS
from .services.profile_service import CHARACTERS, FILTER_KEYS, build_all, build_profile


def _window_and_filters(request):
    window = request.GET.get("window", DEFAULT_WINDOW)
    if window not in WINDOWS:
        window = DEFAULT_WINDOW
    filters = {k: request.GET[k] for k in FILTER_KEYS if request.GET.get(k)}
    return window, filters


def threat_profile(request):
    """Threat-profile page (demo, fed by mock killmails via profile_service).

    Window + filters are query params; every metric is re-aggregated over the
    matching killmail set. To keep filtering snappy the heavy expanded detail is
    NOT in the default payload:
      * `?fragment=blocks` → just the compact rows (swapped on filter/window).
      * `?fragment=detail&char=ID` → one character's detail (loaded on expand).
    """
    window, filters = _window_and_filters(request)
    mode = request.GET.get("fragment")

    if mode == "detail":
        try:
            char_id = int(request.GET.get("char", ""))
        except ValueError:
            raise Http404("bad char id")
        entry = next((c for c in CHARACTERS if c["character"]["id"] == char_id), None)
        if entry is None:
            raise Http404("unknown character")
        context = {"profile": build_profile(entry, window, filters), "target_filter": "target" in filters}
        return render(request, "intel/_char_detail.html", context)

    context = {
        "profiles": build_all(window, filters),
        "is_demo": True,
        "windows": WINDOWS,
        "window_labels": WINDOW_LABELS,
        "window_btn": WINDOW_BTN,
        "window": window,
        "filters": filters,
        "target_filter": "target" in filters,
    }
    template = "intel/_char_blocks.html" if mode else "intel/threat.html"
    return render(request, template, context)
