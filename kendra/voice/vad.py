from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class EnergyVAD:
    """Small zero-download fallback VAD used only until a measured provider is qualified."""

    threshold_rms: float = 450.0

    def is_speech(self, pcm16: np.ndarray) -> bool:
        if pcm16.size == 0:
            return False
        values = pcm16.astype(np.float32)
        rms = math.sqrt(float(np.mean(values * values)))
        return rms >= self.threshold_rms
