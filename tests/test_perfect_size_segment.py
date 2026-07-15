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


def test_longest_match_keeps_a_dictionary_word_whole(tmp_path, monkeypatch):
    wl = tmp_path / "words.txt"
    wl.write_text("ស្រុក\nខ្មែរ\n", encoding="utf-8")
    monkeypatch.setattr(ps, "_WORDS", None)          # reset memoised cache
    words = ps.load_words(str(wl))
    assert "ស្រុក" in words
    toks = ps.segment("ស្រុកខ្មែរ", words=words)      # "srok khmer" = two words
    assert toks == ["ស្រុក", "ខ្មែរ"]                 # not split mid-word


def test_segment_falls_back_to_syllables_off_dictionary(tmp_path, monkeypatch):
    wl = tmp_path / "words.txt"
    wl.write_text("ខ្មែរ\n", encoding="utf-8")
    monkeypatch.setattr(ps, "_WORDS", None)
    words = ps.load_words(str(wl))
    # unknown run still tiles losslessly via syllables
    text = "ស្រុក"
    toks = ps.segment(text, words=words)
    assert "".join(toks) == text
