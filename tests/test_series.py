from manhwaprep import series


def test_comix_url():
    s = series.detect(
        "https://comix.to/title/55kym-why-the-villainess-wields-the-sword/9356816-chapter-1")
    assert s.series_id == "comix:55kym-why-the-villainess-wields-the-sword"
    assert s.series_name == "Why The Villainess Wields The Sword"
    assert s.chapter_number == 1.0
    assert s.chapter_id == "9356816-chapter-1"
    assert s.series_url == "https://comix.to/title/55kym-why-the-villainess-wields-the-sword"


def test_folder_source():
    s = series.detect("/Users/me/Desktop/ManhwaPrep/output/White Demon/chapter-5")
    assert s.series_id == "folder:White Demon"
    assert s.series_name == "White Demon"
    assert s.chapter_id == "chapter-5"
    assert s.chapter_number == 5.0
    assert s.series_url is None


def test_unknown_is_ungrouped():
    s = series.detect("")
    assert s.series_id == "ungrouped"
    assert s.series_name == "Ungrouped"


def test_slugify():
    assert series.slugify("Why the Villainess Wields the Sword!") == \
        "why-the-villainess-wields-the-sword"
    assert series.slugify("") == "untitled"
