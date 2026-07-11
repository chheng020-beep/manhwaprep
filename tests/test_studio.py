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


def test_add_scan_advance_and_prepping_reset():
    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        j = st.add("http://x/ch3", "Broken Ring ch3")
        assert j.state == studio.QUEUED
        assert os.path.isdir(st.chapter_dir(j.slug))

        # simulate a crash mid-prep
        st.set_state(j.slug, studio.PREPPING)
        jobs = st.scan()
        assert len(jobs) == 1
        assert jobs[0].state == studio.QUEUED  # prepping reset on scan

        st.set_state(j.slug, studio.PREPPING)
        st.advance(j.slug)
        assert studio.ChapterJob.from_status(st.chapter_dir(j.slug)).state == studio.TYPESET


def test_error_and_retry():
    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        j = st.add("http://x/ch4", "ch4")
        st.set_error(j.slug, "download failed")
        assert studio.ChapterJob.from_status(st.chapter_dir(j.slug)).state == studio.ERROR
        st.retry(j.slug)
        got = studio.ChapterJob.from_status(st.chapter_dir(j.slug))
        assert got.state == studio.QUEUED and got.error is None


def test_slug_collision_is_unique():
    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        a = st.add("http://x/1", "Same Title")
        b = st.add("http://x/2", "Same Title")
        assert a.slug != b.slug
