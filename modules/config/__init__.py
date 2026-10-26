"""modules.config — 工程根目录 settings.ini + 本目录 style / outcome。"""
from __future__ import annotations

from .defaults import DEFAULT_SETTINGS, merged_settings  # noqa: F401
from .ini_io import apply_to_globals, find_settings_ini, read_ini

_ini = find_settings_ini()
_data = merged_settings(read_ini(_ini) if _ini is not None else None)
apply_to_globals(globals(), _data)

from .style import (  # noqa: E402,F401
    ensure_output_dirs,
    figures_on,
    landmark_roc_on,
    ipcw_roc_on,
    save_plot,
    want_tiff,
    want_pdf,
    apply_fig_font,
    apply_clean_theme,
    style_new_axes,
    ROC_COLOR_MAP,
    ROC_FALLBACK_COLORS,
)
from .outcome import set_outcome, set_pairwise  # noqa: E402,F401
