import sys
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from manhwaprep import perfect_size as ps


def test_syllable_keeps_coeng_cluster_together():
    # ស + coeng + រ + vowel ុ  must stay in ONE syllable, never split the coeng off
    s = "ស្រុក"          # sruk
    parts = ps.syllable_split(s)
    assert "".join(parts) == s
    assert all("្" != p[0] for p in parts)     # no token starts with a bare coeng
    # the coeng+ro subscript must ride with its base, not be its own token
    assert any("្" in p for p in parts)
    assert all(len(p) >= 1 for p in parts)


def test_segment_tiles_text_exactly():
    text = "លោក ១០០ hello"
    toks = ps.segment(text)
    assert "".join(toks) == text                    # lossless tiling


def test_segment_splits_non_khmer_on_spaces():
    toks = ps.segment("hello world")
    assert [t.strip() for t in toks if t.strip()] == ["hello", "world"]
