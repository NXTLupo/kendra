"""Moondream 0.5B via its own ONNX runtime — the encode-once eye.

Her llama.cpp eye has two structural limits this fixes:

1. It cannot answer questions. Through llama.cpp's ``--no-jinja`` path
   Moondream returns captions regardless of what is asked, which is where
   every invented "cigarette" and "wooden box" came from.
2. Every question costs a full look. The image is re-encoded each time,
   about 12 s, so two questions about one moment cost 24 s.

This provider uses Moondream's own package, which splits the work the way
the model actually works: encode the image once (the expensive part), then
ask as many questions as you like against that encoding.

Measured on a real camera frame (0.5B int8, Intel iMac, 6 CPU threads):

    model load                  3.1 s   once at startup
    encode_image                6.9 s   once per frame
    "what is the person holding?"   1.2 s   correct
    "what is the person wearing?"   0.9 s   correct
    free-form "describe this image" 12.5 s  invents detail — avoid

So: short targeted questions, never long descriptions. People counting
stays with YuNet, which is authoritative and was wrong here (it said two
people when there was one).

Runtime is onnxruntime — already used for Parakeet, Kokoro and her
embeddings — so this is the same wheel story on x86_64 macOS and aarch64
Linux. Core Electronics measure ~8 s per frame for this model on a Pi 5.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)


class MoondreamOnnxEye:
    """Lazy-loading Moondream 0.5B with a one-frame encoding cache."""

    def __init__(self, model_path: Path, cache_seconds: float = 90.0):
        self.model_path = Path(model_path)
        self.cache_seconds = float(cache_seconds)
        self._model: Any | None = None
        self._load_lock = threading.Lock()
        # One encoding at a time: the whole point is to reuse it.
        self._encoded: Any | None = None
        self._encoded_signature: Any | None = None
        self._encoded_at = 0.0

    def available(self) -> tuple[bool, str]:
        if not self.model_path.exists():
            return False, f"Moondream model file not found: {self.model_path}"
        try:
            import moondream  # noqa: F401
        except ImportError:
            return False, "the moondream package is not installed (pip install moondream==0.0.6)"
        return True, "ok"

    def load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                import moondream as md

                started = time.time()
                self._model = md.vl(model=str(self.model_path))
                LOG.info("Moondream 0.5B loaded in %.1fs", time.time() - started)
        return self._model

    def warm(self) -> None:
        """Pay the load cost at startup, never on Jonathan's turn."""
        try:
            self.load()
        except Exception:
            LOG.debug("Moondream warm-up skipped", exc_info=True)

    def _encode(self, frame_bgr, signature) -> Any:
        """Encode a frame, reusing the last encoding for an unchanged scene."""
        import numpy as np
        from PIL import Image

        fresh = (
            self._encoded is not None
            and self._encoded_signature is not None
            and time.time() - self._encoded_at <= self.cache_seconds
            and float(np.abs(signature - self._encoded_signature).mean()) <= 6.0
        )
        if fresh:
            LOG.info("Moondream: reusing the existing frame encoding")
            return self._encoded

        model = self.load()
        rgb = frame_bgr[:, :, ::-1]  # OpenCV BGR -> RGB
        started = time.time()
        encoded = model.encode_image(Image.fromarray(rgb))
        LOG.info("Moondream: encoded a new frame in %.1fs", time.time() - started)
        self._encoded = encoded
        self._encoded_signature = signature
        self._encoded_at = time.time()
        return encoded

    def ask(self, frame_bgr, signature, question: str) -> str:
        """Answer one short question about a frame."""
        encoded = self._encode(frame_bgr, signature)
        model = self.load()
        started = time.time()
        answer = model.query(encoded, question.strip())["answer"]
        LOG.info("Moondream: %r answered in %.1fs", question[:40], time.time() - started)
        return str(answer).strip()

    def ask_guarded(self, frame_bgr, signature, question: str) -> str:
        """Kept for interface stability; grounding is enforced generally.

        An earlier version carried a hand-written table of preconditions
        (fingers, text, ...). That is exactly the use-case-specific coding
        this project rejects: every new question type would need another
        entry. Truthfulness is enforced once, generally, by checking her
        ANSWER against what her eyes actually reported — the same guard used
        for research evidence.
        """
        return self.ask(frame_bgr, signature, question)

    def caption(self, frame_bgr, signature) -> str:
        """A short scene caption. Kept brief: long free-form output invents."""
        return self.ask(frame_bgr, signature, "Describe this image in one sentence.")
