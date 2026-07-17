import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from manhwaprep.typeset_editor import TextBoxItem
_app = QApplication.instance() or QApplication([])


def test_apply_perfect_size_targets_a_big_size_and_grows_height():
    import manhwaprep.perfect_size as ps
    w0 = 300
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ", 0, 0, w0, 60)
    it.raw_text = it.text
    it.apply_perfect_size()
    assert it.fitted is True
    # big font, capped at 40, never larger; width fixed (no spill), height grew
    assert 12.0 <= it.max_size <= ps.PREFER_SIZES[0]
    assert abs(it.w - w0) < 1.0                # box width unchanged — never spills
    assert "\n" in it.text                     # long text wraps to several lines
    assert it.h > 60                           # short box grew to fit the text


def test_fitted_box_refit_is_noop_on_height():
    it = TextBoxItem(1, "លោក", 0, 0, 200, 120)
    it.raw_text = it.text
    it.apply_perfect_size()
    h = it.h
    it._refit()
    assert abs(it.h - h) < 1.0


def test_fitted_box_height_is_draggable_via_refit_min_h():
    """Regression: _refit() early-returned for fitted boxes without ever
    honoring min_h/top/bottom, so a top/bottom edge drag (which calls
    _refit(min_h=..., top=.../bottom=...)) was inert on a perfect-sized box.
    The fitted branch must now also apply an explicit min_h before returning."""
    it = TextBoxItem(1, "លោក", 0, 0, 200, 120)
    it.raw_text = it.text
    it.apply_perfect_size()
    assert it.fitted is True

    it._refit(min_h=400)
    assert it.h == 400

    # control: without min_h, height stays unchanged (existing no-op behavior)
    it._refit()
    assert it.h == 400


def test_fitted_box_restores_font_size_after_reload():
    """Regression: a saved fitted box reconstructed via the editor's load path
    (TextBoxItem(...) -> font rebuilt from d["font"] -> _refit()) must restore
    the perfect-sized font, not silently keep Qt's default 12pt. _refit()
    early-returns for fitted boxes, so it must be the one place that reapplies
    max_size to the rebuilt font before returning."""
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ", 0, 0, 300, 200)
    it.raw_text = it.text
    it.apply_perfect_size()
    saved_size = it.max_size
    assert saved_size > 12.0   # sanity: perfect-size actually picked a real size

    d = it.to_dict()

    # Mirror the editor's load path (_rebuild_from_state) exactly:
    reloaded = TextBoxItem(d["n"], d["text"], d["x"], d["y"], d["w"], d["h"])
    reloaded.raw_text = d.get("raw_text", d["text"])
    reloaded.fitted = bool(d.get("fitted", False))
    reloaded.font = QFont(d["font"])          # resets pointSizeF to Qt default (12.0)
    reloaded.max_size = float(d["size"])
    reloaded._refit()

    assert reloaded.fitted is True
    assert abs(reloaded.font.pointSizeF() - saved_size) < 0.01


def test_nonfitted_box_still_shrinks_when_text_cleared():
    """Regression for the side-panel text-commit handler (_text_changed):
    apply_perfect_size() is a no-op on empty text (it returns before setting
    `fitted`), so clearing the text of a box that was never perfect-size-fitted
    must fall back to _refit() to shrink the box height — mirroring the exact
    guarded sequence `apply_perfect_size(); if not it.fitted: it._refit()`
    used at both call sites in typeset_editor.py."""
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណនិងសូមជូនពរ", 0, 0, 300, 200)
    it.raw_text = it.text
    it._refit()               # grows the box to fit the long, non-fitted text
    tall_h = it.h
    assert it.fitted is False  # never called apply_perfect_size -> never fitted

    # Simulate the side-panel commit: clear the text, then run the guarded
    # sequence exactly as _text_changed/_commit_inline do.
    it.text = ""
    it.raw_text = it.text
    it.apply_perfect_size()     # no-op on empty text; must NOT crash or set fitted
    assert it.fitted is False
    if not it.fitted:
        it._refit()

    assert it.h < tall_h       # box shrank back down instead of staying tall
    assert it.h >= 8.0         # sane minimum height floor (see _refit)


def test_resize_rewraps_a_fitted_box():
    import manhwaprep.perfect_size as ps
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ", 0, 0, 300, 200)
    it.raw_text = it.text
    it.apply_perfect_size()
    lines1 = it.text.count("\n") + 1
    # target-size model: font stays a preferred size; a WIDER box fits more per
    # line, so it re-wraps into fewer lines (rather than changing the font).
    it.w = 700
    it.apply_perfect_size()
    assert it.max_size in ps.PREFER_SIZES
    assert it.text.count("\n") + 1 <= lines1


def test_inline_manual_line_breaks_are_honored():
    """Regression for the inline-edit 'drop a line' bug: a newline the user types
    while editing on the panel (Enter) is stored in raw_text by _commit_inline,
    then apply_perfect_size() runs. The auto-balancer used to re-segment raw_text
    and compute its OWN breaks, discarding the manual newline ('input went in but
    auto-resize pulls it back'). When raw_text carries explicit newlines they must
    be honored as the fitted lines, not re-wrapped."""
    # A wide box: both short words fit comfortably on ONE line, so the auto
    # balancer would collapse them -> the only way to get two lines is to honor
    # the manual break.
    it = TextBoxItem(1, "", 0, 0, 400, 120)
    it.text = "លោក\nអ្នក"
    it.raw_text = "លោក\nអ្នក"        # exactly what _commit_inline stores after Enter
    it.apply_perfect_size()
    assert it.fitted is True
    assert it.text.count("\n") == 1   # the user's single break survived
    layout, _ = it._text_layout()
    assert layout.lineCount() == 2    # and it renders as two visual lines


def test_autowrap_still_applies_without_manual_breaks():
    """Control: text with NO manual newline in raw_text must still auto-balance
    into multiple lines (the honor-breaks path must not disable auto-wrapping)."""
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ", 0, 0, 200, 60)
    it.raw_text = it.text             # unbroken source (no '\n')
    it.apply_perfect_size()
    assert it.fitted is True
    assert "\n" in it.text            # auto-wrap still kicks in


def test_fitted_box_renders_one_visual_line_per_fitted_line():
    """RENDER-level regression: apply_perfect_size() stores fitted lines joined
    by '\\n' in self.text, but QTextLayout does NOT treat '\\n' as a line break
    (unlike QTextDocument). Combined with NoWrap for fitted boxes, this used to
    collapse a multi-line fit into ONE visual line that overflowed the box
    horizontally instead of wrapping. This test renders the actual QTextLayout
    (as the fill/outline painters do) and asserts the line count the layout
    itself produces, not just that '\\n' appears in the stored text."""
    it = TextBoxItem(
        1,
        "លោកអ្នកទាំងអស់គ្នាសូមអរគុណដែលបានចូលរួមក្នុងកម្មវិធីនេះ",
        0, 0, 220, 260,
    )
    it.raw_text = it.text
    it.apply_perfect_size()
    expected_lines = it.text.count("\n") + 1
    assert expected_lines >= 3   # sanity: the fit actually produced multiple lines

    layout, _ = it._text_layout()
    assert layout.lineCount() == expected_lines


def test_new_textbox_default_size_is_40():
    it = TextBoxItem(1, "x", 0, 0, 100, 40)
    assert it.max_size == 40.0


def test_fitted_box_grows_height_when_font_enlarged():
    """Regression: raising the font on a perfect-sized box must grow the box so
    the taller text isn't clipped at the bottom (the Size-box path)."""
    from PySide6.QtGui import QFontMetricsF
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណនិងសូមជូនពរ", 0, 0, 300, 200)
    it.raw_text = it.text
    it.apply_perfect_size()               # fitted (~35pt)
    h_small = it.h
    # editor's Size-box path: set max_size, refit, regrow
    it.max_size = 70.0
    it._refit()
    it._grow_fitted_height()
    lines = it.text.split("\n")
    need = len(lines) * QFontMetricsF(it.font).lineSpacing() * it.line_spacing
    assert it.h >= need                   # tall enough -> no bottom clip
    assert it.h > h_small                 # grew with the bigger font
