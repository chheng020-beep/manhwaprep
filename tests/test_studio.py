import os, sys, tempfile
sys.path.insert(0, '/Users/leapheakuoch/ManhwaPrep')
from manhwaprep import studio


def test_slugify_is_filesystem_safe():
    assert studio.slugify("The Broken Ring: ch 3!") == "the-broken-ring-ch-3"
    assert studio.slugify("  多 spaces  ") != ""  # never empty


def test_job_status_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        job = studio.ChapterJob(title="T", source="http://x/c3", slug="t",
                                state=studio.TYPESET)
        job.to_status(d)
        assert os.path.exists(os.path.join(d, "status.json"))
        back = studio.ChapterJob.from_status(d)
        assert back.title == "T" and back.source == "http://x/c3"
        assert back.state == studio.TYPESET
        assert back.updated_at  # stamped on write
