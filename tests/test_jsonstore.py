import os
from manhwaprep import jsonstore


def test_roundtrip_and_default(tmp_path):
    p = os.path.join(tmp_path, "reg.json")
    assert jsonstore.read_json(p, {"x": 1}) == {"x": 1}      # missing -> default
    jsonstore.atomic_write(p, {"a": [1, 2], "b": "café"})
    assert jsonstore.read_json(p, None) == {"a": [1, 2], "b": "café"}


def test_corrupt_returns_default(tmp_path):
    p = os.path.join(tmp_path, "bad.json")
    with open(p, "w") as f:
        f.write("{not json")
    assert jsonstore.read_json(p, []) == []


def test_locked_serializes_access(tmp_path):
    p = os.path.join(tmp_path, "reg.json")
    with jsonstore.locked(p):
        jsonstore.atomic_write(p, {"n": 1})
    with jsonstore.locked(p):
        assert jsonstore.read_json(p, None) == {"n": 1}
