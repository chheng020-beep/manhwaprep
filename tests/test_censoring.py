import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
import numpy as np
from manhwaprep import nsfw


def test_pixelate_keeps_shape_and_obscures():
    # a smooth horizontal gradient -> pixelation must quantise it into blocks
    region = np.zeros((100, 100, 3), np.uint8)
    region[:, :, 0] = np.linspace(0, 255, 100, dtype=np.uint8)[None, :]
    out = nsfw.pixelate(region, blocks=10)
    assert out.shape == region.shape
    # far fewer distinct columns after mosaicking than the 100-step gradient
    assert len(np.unique(out[50, :, 0])) <= 12
    assert not np.array_equal(out, region)


def test_detect_filters_to_fb_safe_and_maps_boxes():
    class FakeDetector:
        def detect(self, path):
            return [
                {"class": "FEMALE_BREAST_EXPOSED", "score": 0.9, "box": [10, 20, 30, 40]},
                {"class": "FACE_FEMALE", "score": 0.99, "box": [0, 0, 5, 5]},      # not FB-safe
                {"class": "BUTTOCKS_EXPOSED", "score": 0.10, "box": [1, 2, 3, 4]}, # below min_score
            ]
    img = np.zeros((80, 80, 3), np.uint8)
    boxes = nsfw.detect(img, detector=FakeDetector(), min_score=0.35)
    assert boxes == [{"x": 10, "y": 20, "w": 30, "h": 40, "source": "auto"}]


def test_labels_are_the_fb_safe_set():
    assert nsfw.LABELS == {
        "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
        "FEMALE_BREAST_EXPOSED", "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
    }
