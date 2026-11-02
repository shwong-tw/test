#!/usr/bin/env python3
"""
Slide-change detection shared by browser_download.py and zoom_slides.py.

Screenshots are reduced to small grayscale thumbnails (thumbnail()) and fed to
SlideDetector, which decides which ones show a new slide. Needs Pillow and
numpy; no browser.
"""

import io


def fmt_time(seconds):
    s = int(seconds)
    return f"{s // 3600:02d}h{s % 3600 // 60:02d}m{s % 60:02d}s"


# --------------------------------------------------------------------------
# Slide-change detection (Pillow + numpy, no browser needed)
# --------------------------------------------------------------------------

THUMB_SIZE = (256, 144)


def thumbnail(png_bytes, size=THUMB_SIZE):
    """Grayscale, downscaled copy of a screenshot as a numpy array."""
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes)).convert("L").resize(size, Image.BOX)
    return np.asarray(img, dtype=np.int16)


def is_blank(thumb, max_mean=16.0, min_stddev=2.0):
    """True for dark, (nearly) uniform frames, e.g. black frames while loading."""
    return thumb.mean() < max_mean and thumb.std() < min_stddev


class SlideDetector:
    """
    Decide which sampled frames to keep.

    Frames are compared by the share of pixels that changed noticeably
    (more than `pixel_delta` grey levels), so a new bullet or line of text
    counts even though it is a small part of the picture.

    - A frame is kept when more than `threshold` % of the pixels differ from
      the last kept frame, and the picture has settled (at most `stable` %
      changed since the previous sample) -- this skips mid-fade frames.
    - Areas that keep changing (e.g. a speaker's webcam inset) are learned
      from the last `history` samples and left out of the comparison.
    - If the picture never settles, the frame is kept anyway after
      `max_unstable` samples, so a slide is not missed because of motion.
    - Blank (black) frames are never kept.
    """

    def __init__(self, threshold=0.5, pixel_delta=25, stable=None, max_unstable=4,
                 history=20, motion=0.3):
        from collections import deque
        self.threshold = threshold
        self.pixel_delta = pixel_delta
        self.stable = threshold / 2 if stable is None else stable
        self.max_unstable = max_unstable
        self.motion = motion
        self.steps = deque(maxlen=history)
        self.moving = None
        self.last_saved = None
        self.prev = None
        self.unstable = 0

    def _changed(self, a, b):
        import numpy as np
        return np.abs(a - b) > self.pixel_delta

    def _percent(self, mask):
        if self.moving is not None:
            still = ~self.moving
            return 100.0 * (mask & still).sum() / max(1, still.sum())
        return 100.0 * mask.mean()

    def _save(self, thumb):
        self.last_saved = thumb
        self.unstable = 0
        return True

    def feed(self, thumb):
        import numpy as np
        prev, self.prev = self.prev, thumb
        step = None
        if prev is not None:
            step = self._changed(thumb, prev)
            self.steps.append(step)
            if len(self.steps) >= 5:
                self.moving = np.mean(self.steps, axis=0) >= self.motion
        if is_blank(thumb):
            return False
        settled = step is None or self._percent(step) <= self.stable
        if self.last_saved is None:
            return self._save(thumb) if settled else False
        if self._percent(self._changed(thumb, self.last_saved)) <= self.threshold:
            self.unstable = 0
            return False
        if settled or self.unstable + 1 >= self.max_unstable:
            return self._save(thumb)
        self.unstable += 1
        return False
