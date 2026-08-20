from __future__ import annotations

import logging
import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

from ..config import Settings
from .vad import EnergyVAD

LOG = logging.getLogger(__name__)


def _mean_rms(pcm: np.ndarray, block: int = 480) -> tuple[float, float]:
    """Return (mean, peak) RMS over fixed-size blocks of int16 audio."""
    values = pcm.astype(np.float32)
    usable = (len(values) // block) * block
    if usable == 0:
        return 0.0, 0.0
    blocks = values[:usable].reshape(-1, block)
    rms = np.sqrt((blocks * blocks).mean(axis=1))
    return float(rms.mean()), float(rms.max())


class LocalAudioCapture:
    """Microphone capture with device auto-selection and ambient calibration.

    Kendra cannot assume the operating system's default input is a microphone.
    On the development iMac the OS default turned out to be a USB guitar
    amplifier that delivers permanent digital silence, which made every voice
    feature look broken while every log looked healthy. On the robot the Pi's
    default can just as easily be an HDMI or dummy input. So with
    ``voice.capture.device: auto`` Kendra probes every input device briefly,
    picks the one that actually carries a signal, and logs the choice plus the
    measured noise floor. The VAD speech threshold is then calibrated from
    that noise floor instead of trusting a hardcoded number.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.sample_rate = int(settings.get("voice.capture.sample_rate", 16000))
        self.channels = int(settings.get("voice.capture.channels", 1))
        self.max_seconds = float(settings.get("voice.capture.seconds", 10))
        self._configured_device = settings.get("voice.capture.device", "auto")
        self.device: int | str | None = None
        self.vad = EnergyVAD(float(settings.get("voice.vad.threshold_rms", 450)))
        self.auto_calibrate = bool(settings.get("voice.vad.auto_calibrate", True))
        self.start_timeout = float(settings.get("voice.vad.start_timeout_seconds", 3.0))
        self.silence_seconds = float(settings.get("voice.vad.silence_seconds", 0.8))
        self._device_lock = threading.Lock()
        self._device_ready = False
        # Diagnostics for the last capture, so callers can distinguish "heard
        # speech" from "timed out listening to silence" and logs can say why.
        self.last_capture_speech = False
        self.last_capture_peak = 0.0

    def _sd(self):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Install the voice extra: pip install -e '.[voice]'") from exc
        return sd

    def _probe(self, sd, device: int | None, seconds: float = 0.6) -> tuple[float, float]:
        recording = sd.rec(
            int(seconds * self.sample_rate),
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            device=device,
        )
        sd.wait()
        return _mean_rms(recording.flatten())

    def _ensure_device(self) -> None:
        """Resolve and calibrate the input device once per service lifetime."""
        if self._device_ready:
            return
        with self._device_lock:
            if self._device_ready:
                return
            sd = self._sd()
            configured = self._configured_device
            resolved: int | str | None = None
            if configured not in {None, "", "default", "auto"}:
                # Explicit index or name substring from config wins when the
                # device is actually present. USB interfaces come and go, so a
                # missing pin must degrade to auto-probing, never crash the
                # voice service into a silent wake loop.
                if isinstance(configured, str) and not configured.isdigit():
                    for index, info in enumerate(sd.query_devices()):
                        if (
                            int(info.get("max_input_channels", 0)) > 0
                            and configured.lower() in str(info["name"]).lower()
                        ):
                            resolved = index
                            LOG.info("Pinned microphone [%d] %s", index, info["name"])
                            break
                    if resolved is None:
                        LOG.error(
                            "Pinned microphone %r is not connected; auto-probing instead",
                            configured,
                        )
                else:
                    resolved = configured
            if resolved is not None:
                self.device = resolved
            else:
                best: tuple[float, float, int, str] | None = None
                for index, info in enumerate(sd.query_devices()):
                    if int(info.get("max_input_channels", 0)) < 1:
                        continue
                    try:
                        mean, peak = self._probe(sd, index)
                    except Exception as exc:
                        LOG.info("Input device [%d] %s unusable: %s", index, info["name"], exc)
                        continue
                    LOG.info(
                        "Input device [%d] %s ambient RMS mean=%.0f peak=%.0f",
                        index, info["name"], mean, peak,
                    )
                    if mean > 1.0 and (best is None or mean > best[0]):
                        best = (mean, peak, index, str(info["name"]))
                if best is None:
                    LOG.error(
                        "Every input device delivered silence; falling back to the system "
                        "default. Kendra will not hear speech until a live microphone is "
                        "selected (voice.capture.device) or authorized."
                    )
                    self.device = None
                else:
                    mean, peak, index, name = best
                    self.device = index
                    LOG.info("Selected microphone [%d] %s (ambient RMS %.0f)", index, name, mean)
                    if self.auto_calibrate:
                        # Speech must clear the room's real noise floor, not a
                        # number tuned on different hardware. 4x the ambient
                        # floor, clamped to a sane range.
                        # Ceiling 900: normal speech RMS on these mics is
                        # 2000-9000, machine-room ambient under fan load can
                        # reach ~1000. A threshold above ~900 starts eating
                        # quiet speech, which is worse than a rare false start.
                        # One noisy window (a passing truck, her own speaker
                        # tail, a guitar chord) used to pin this at the 900
                        # ceiling and leave her deaf to ordinary speech for
                        # the rest of the session. Use the quietest of the
                        # samples and cap far lower.
                        # Three short listens, keep the QUIETEST: one noisy
                        # window must not deafen her for the whole session.
                        floors = [mean]
                        for _ in range(2):
                            try:
                                floors.append(self._probe(sd, index)[0])
                            except Exception:
                                break
                        floor = min(floors)
                        threshold = float(np.clip(floor * 3.0, 120.0, 550.0))
                        LOG.info(
                            "VAD threshold calibrated to %.0f (configured %.0f, quietest ambient %.0f of %s)",
                            threshold, self.vad.threshold_rms, floor,
                            [round(f) for f in floors],
                        )
                        self.vad.threshold_rms = threshold
            self._device_ready = True


    def _open_input_stream(self, sd, **kwargs):
        """Open a microphone stream, recovering from CoreAudio wedges.

        macOS PortAudio fails with -9986 ("Internal PortAudio error") when
        input streams are opened and closed rapidly — which is exactly what
        follow-up listening does. Kendra went fully deaf this way while every
        service reported healthy. Back off, re-probe the device, and try
        again rather than spinning on a dead handle.
        """
        last: Exception | None = None
        for attempt in range(3):
            try:
                return sd.RawInputStream(**kwargs)
            except Exception as exc:  # PortAudioError and friends
                last = exc
                LOG.warning("Microphone stream open failed (attempt %d): %s", attempt + 1, exc)
                time.sleep(0.4 * (attempt + 1))
                if attempt == 1:
                    # Second failure: the cached device index may be stale.
                    try:
                        sd._terminate()
                        sd._initialize()
                    except Exception:
                        LOG.debug("PortAudio reinit failed", exc_info=True)
                    self._device_ready = False
                    try:
                        self._ensure_device(sd)
                    except Exception:
                        LOG.debug("device re-probe failed", exc_info=True)
                    kwargs["device"] = self.device
        # In-process recovery is not enough. Once macOS wedges CoreAudio for
        # a process (PaErrorCode -9986), reinitialising PortAudio does not
        # clear it — she stayed deaf for 68 minutes with every service
        # reporting healthy, and even the wake word could not reach her.
        # Only a fresh process fixes it, and nothing supervises this one, so
        # she restarts herself.
        self._stream_failures = getattr(self, "_stream_failures", 0) + 1
        if self._stream_failures >= 3:
            self._self_restart()
        raise RuntimeError(f"microphone unavailable after retries: {last}")

    def _self_restart(self) -> None:
        """Re-exec this service so CoreAudio starts clean.

        Guarded against loops: never within 90s of start-up, and at most
        once every 5 minutes (tracked in the environment so it survives the
        exec). If the microphone is genuinely absent, she keeps running deaf
        and says so in diagnostics rather than thrashing.
        """
        import os
        import sys

        now = time.time()
        started = float(os.environ.get("KENDRA_VOICE_STARTED_AT") or 0.0)
        last_restart = float(os.environ.get("KENDRA_VOICE_LAST_RESTART") or 0.0)
        if started and now - started < 90:
            LOG.error("Microphone unavailable but service is too young to restart")
            return
        if last_restart and now - last_restart < 300:
            LOG.error("Microphone unavailable; already restarted recently, staying up deaf")
            return
        LOG.error("Microphone unrecoverable (CoreAudio wedge) — restarting the voice service")
        os.environ["KENDRA_VOICE_LAST_RESTART"] = str(now)
        os.environ["KENDRA_VOICE_STARTED_AT"] = str(now)
        try:
            os.execv(sys.executable, [sys.executable, "-m", "kendra", *sys.argv[1:]])
        except Exception:
            LOG.exception("Self-restart failed")

    def wait_for_wake(self, wake_provider, stop_provider=None, cancel_event: threading.Event | None = None) -> str:
        self._ensure_device()
        sd = self._sd()
        block = int(self.settings.get("voice.wake.block_samples", 1280))
        # Tail buffer: audio spoken in the gap between wake detection and the
        # capture stream opening was being lost ("Kendra, TAKE A look..."
        # arrived as "look..."). Keep ~1.2s so capture can seed from it.
        tail_blocks = max(1, int(1.2 * self.sample_rate / block))
        self.wake_tail = deque(maxlen=tail_blocks)
        with self._open_input_stream(sd, **dict(
            samplerate=self.sample_rate,
            blocksize=block,
            device=self.device,
            channels=1,
            dtype="int16",
        )) as stream:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return "cancel"
                data, _overflowed = stream.read(block)
                self.wake_tail.append(bytes(data))
                pcm = np.frombuffer(data, dtype=np.int16)
                if stop_provider is not None:
                    stop_score = float(stop_provider.predict(pcm))
                    threshold = float(self.settings.get("voice.stop_wake.threshold", 0.60))
                    if stop_score >= threshold:
                        return "stop"
                wake_score = float(wake_provider.predict(pcm))
                threshold = float(self.settings.get("voice.wake.threshold", 0.55))
                if wake_score >= threshold:
                    return "wake"

    def capture_utterance(
        self,
        output: Path,
        start_timeout: float | None = None,
        threshold_multiplier: float = 1.0,
        on_speech_start=None,
    ) -> Path:
        self._ensure_device()
        sd = self._sd()
        block = max(160, int(self.sample_rate * 0.03))
        frames: list[bytes] = []
        # Pre-roll: the energy VAD only fires once speech crosses the
        # threshold, which decapitates the first word ("Ask me a question"
        # was heard as "me a question" and Kendra looked amnesiac). Keep the
        # most recent ~0.6s of audio and prepend it when speech starts.
        preroll_blocks = max(1, int(0.6 * self.sample_rate / block))
        preroll: deque[bytes] = deque(maxlen=preroll_blocks)
        # Seed with the wake stream's tail so words spoken across the stream
        # handoff survive.
        wake_tail = getattr(self, "wake_tail", None)
        if wake_tail:
            preroll.extend(wake_tail)
            wake_tail.clear()
        speech_started = False
        peak = 0.0
        speech_start_deadline = time.monotonic() + (start_timeout if start_timeout is not None else self.start_timeout)
        absolute_deadline = time.monotonic() + self.max_seconds
        silent_duration = 0.0

        with self._open_input_stream(sd, **dict(
            samplerate=self.sample_rate,
            blocksize=block,
            device=self.device,
            channels=1,
            dtype="int16",
        )) as stream:
            overflow_blocks = 0
            while time.monotonic() < absolute_deadline:
                data, _overflowed = stream.read(block)
                if _overflowed:
                    # Starved CPU = dropped audio = garbage transcripts.
                    # Count and report instead of silently mangling speech.
                    overflow_blocks += 1
                raw = bytes(data)
                pcm = np.frombuffer(raw, dtype=np.int16)
                _, block_peak = _mean_rms(pcm, block=len(pcm) or 1)
                peak = max(peak, block_peak)
                speaking = self.vad.is_speech(pcm) and (
                    threshold_multiplier <= 1.0
                    or _mean_rms(pcm, block=len(pcm) or 1)[1]
                    >= self.vad.threshold_rms * threshold_multiplier
                )
                if speaking:
                    if not speech_started:
                        frames.extend(preroll)
                        preroll.clear()
                        if on_speech_start is not None:
                            # Jonathan started talking: kill the thinking
                            # blips instantly — tones over his voice read as
                            # her not listening.
                            try:
                                on_speech_start()
                            except Exception:
                                pass
                    speech_started = True
                    silent_duration = 0.0
                    frames.append(raw)
                elif speech_started:
                    frames.append(raw)
                    silent_duration += block / self.sample_rate
                    # Dynamic endpointing (latency spec rec #4): a fixed 0.8s
                    # trailing silence taxes EVERY turn. Once he has clearly
                    # said something (>1.2s of speech), 0.45s of silence is
                    # decisive; only short fragments keep the longer window
                    # so mid-sentence breaths don't cut him off.
                    spoken_seconds = len(frames) * block / self.sample_rate
                    window = self.silence_seconds if spoken_seconds < 1.2 else max(
                        0.45, self.silence_seconds * 0.55
                    )
                    if silent_duration >= window:
                        break
                elif time.monotonic() >= speech_start_deadline:
                    break
                else:
                    preroll.append(raw)

        if overflow_blocks:
            LOG.warning(
                "Audio capture dropped %d blocks (CPU starvation) — "
                "transcription of this utterance is unreliable",
                overflow_blocks,
            )
        self.last_capture_speech = speech_started
        self.last_capture_peak = peak
        if speech_started:
            LOG.info(
                "Captured %.1fs of speech (peak RMS %.0f, threshold %.0f)",
                len(frames) * block / self.sample_rate, peak, self.vad.threshold_rms,
            )
        else:
            LOG.warning(
                "No speech detected before the %.1fs start timeout on device %r "
                "(peak RMS %.0f vs threshold %.0f)",
                self.start_timeout, self.device, peak, self.vad.threshold_rms,
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(b"".join(frames))
        return output
