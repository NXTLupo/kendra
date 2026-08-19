from __future__ import annotations

import asyncio
import logging
import platform
import re
import threading
import wave
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..connectivity import assert_loopback_http_url
from ..paths import resolve_path

LOG = logging.getLogger(__name__)


def _empty_wav(wav_path: Path) -> bool:
    try:
        with wave.open(str(wav_path), "rb") as wav:
            return wav.getnframes() == 0
    except (EOFError, wave.Error):
        # Let the engine produce the useful format error instead.
        return False


class WhisperCppASR:
    """Local whisper.cpp transcription.

    This is Kendra's portable ASR: whisper.cpp builds from source on both
    x86_64 macOS and aarch64 Linux, so the Intel iMac and the Raspberry Pi run
    the same engine with the same model file.
    """

    provider_name = "whisper_cpp"

    def __init__(self, settings: Settings):
        self.cli = resolve_path(settings.require("voice.asr.whisper_cli"), settings.root)
        self.model = resolve_path(settings.require("voice.asr.model"), settings.root)
        self.threads = int(settings.get("voice.asr.threads", 4))
        self.model_arch = self.model.name

    def available(self) -> tuple[bool, str]:
        if not self.cli.exists():
            return False, f"whisper-cli not found: {self.cli}"
        if not self.model.exists():
            return False, f"Whisper model not found: {self.model}"
        return True, "ok"

    async def transcribe(self, wav_path: Path) -> str:
        ok, reason = self.available()
        if not ok:
            raise FileNotFoundError(reason)
        if _empty_wav(wav_path):
            return ""
        process = await asyncio.create_subprocess_exec(
            str(self.cli),
            "-m",
            str(self.model),
            "-t",
            str(self.threads),
            "-nt",
            "-np",
            str(wav_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"whisper-cli failed: {stderr.decode(errors='replace')[-2000:]}")
        text = stdout.decode("utf-8", errors="replace").strip()
        return re.sub(r"\s+", " ", text).strip()


class WhisperServerASR(WhisperCppASR):
    """Persistent local whisper.cpp server with automatic CLI fallback.

    whisper-cli reloads the 145 MB model on every call, costing roughly a
    second of dead air per spoken turn. whisper-server keeps the model
    resident behind a loopback HTTP endpoint; measured warm inference on the
    Intel iMac is ~0.86 s for a 2.5 s utterance versus ~1.66 s via the CLI.
    The same whisper.cpp tree builds the server on aarch64 Linux, so the Pi
    runs the identical binary under systemd (kendra-asr.service).

    If the server is unreachable the turn silently degrades to the CLI path
    inherited from WhisperCppASR: slower, never broken.
    """

    provider_name = "whisper_server"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.base_url = str(
            settings.get("voice.asr.server_url", "http://127.0.0.1:8082")
        ).rstrip("/")
        assert_loopback_http_url(self.base_url)
        self.timeout = float(settings.get("voice.asr.server_timeout_seconds", 30))

    async def _server_alive(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{self.base_url}/")
                return response.status_code < 500
        except httpx.HTTPError:
            return False

    def available(self) -> tuple[bool, str]:
        # The CLI fallback makes file presence the real availability test;
        # server liveness only changes speed. Report which mode is active.
        ok, reason = super().available()
        if not ok:
            return False, reason
        return True, "ok (server with cli fallback)"

    async def transcribe(self, wav_path: Path) -> str:
        if not wav_path.is_file():
            raise FileNotFoundError(wav_path)
        if _empty_wav(wav_path):
            return ""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                with wav_path.open("rb") as handle:
                    response = await client.post(
                        f"{self.base_url}/inference",
                        files={"file": (wav_path.name, handle, "audio/wav")},
                        data={"response_format": "json"},
                    )
                response.raise_for_status()
                text = str(response.json().get("text", ""))
                return re.sub(r"\s+", " ", text).strip()
        except (httpx.HTTPError, ValueError) as exc:
            LOG.warning(
                "whisper-server unavailable (%s: %s); falling back to whisper-cli",
                type(exc).__name__, exc,
            )
            return await super().transcribe(wav_path)


class ParakeetOnnxASR(WhisperServerASR):
    """NVIDIA Parakeet TDT 0.6B v3, int8 ONNX, on CPU via onnx-asr.

    Measured on the Intel iMac against the same utterance: 0.30 s warm versus
    3.4 s for whisper small.en under load — with identical transcription and a
    better published WER. Runs in-process through onnxruntime (the same
    runtime as her memory embeddings), pure-Python ``onnx-asr`` loader, both
    x86_64 macOS and Linux aarch64 wheels — full Pi parity, no server needed.
    Any failure falls back to whisper-server, then whisper-cli, inherited
    from WhisperServerASR: slower, never deaf.
    """

    provider_name = "parakeet_onnx"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.parakeet_dir = resolve_path(
            settings.get("voice.asr.parakeet_dir", "./models/parakeet/parakeet-tdt-0.6b-v3-onnx"),
            settings.root,
        )
        self._parakeet: Any | None = None
        self._parakeet_lock = threading.Lock()
        self._parakeet_broken = False

    def available(self) -> tuple[bool, str]:
        if (self.parakeet_dir / "encoder-model.int8.onnx").is_file():
            return True, "ok (parakeet with whisper fallback)"
        return super().available()

    def _load_parakeet(self) -> Any:
        with self._parakeet_lock:
            if self._parakeet is None:
                import onnx_asr

                self._parakeet = onnx_asr.load_model(
                    "nemo-parakeet-tdt-0.6b-v3",
                    path=self.parakeet_dir,
                    quantization="int8",
                    # CPU everywhere: identical behavior on iMac and Pi, and
                    # macOS CoreML fails on this model's dynamic shapes.
                    providers=["CPUExecutionProvider"],
                )
            return self._parakeet

    async def transcribe(self, wav_path: Path) -> str:
        if not wav_path.is_file():
            raise FileNotFoundError(wav_path)
        if _empty_wav(wav_path):
            return ""
        if not self._parakeet_broken and (self.parakeet_dir / "encoder-model.int8.onnx").is_file():
            try:
                model = await asyncio.to_thread(self._load_parakeet)
                text = await asyncio.to_thread(model.recognize, str(wav_path))
                return re.sub(r"\s+", " ", str(text)).strip()
            except Exception as exc:
                self._parakeet_broken = True
                LOG.warning(
                    "Parakeet ASR failed (%s: %s); falling back to whisper for this session",
                    type(exc).__name__, exc,
                )
        return await super().transcribe(wav_path)


class MoonshineOnnxASR(WhisperServerASR):
    """Moonshine Base via plain onnxruntime — the Pi RAM-relief option.

    ST Micro's 1000-run benchmark (docs/EDGE_PIPELINE_BENCHMARK_ANALYSIS.md):
    variable-length attention, no 30s zero-padding, RTF 0.064 and 1.4 J/s on
    CPU with WER 0.051. Roughly 250 MB resident versus Parakeet's ~700 MB —
    the documented fallback if the Pi's memory budget tightens. Parakeet
    stays the default (better WER).

    Implemented self-contained (encoder + merged decoder, greedy) because
    the upstream ``useful-moonshine-onnx`` package drags librosa→numba→
    llvmlite, which fails to build on Intel macOS and is hostile on Pi
    aarch64. Moonshine consumes raw 16 kHz float audio — no mel frontend —
    so onnxruntime + tokenizers (already shipped for Parakeet and the
    embedding brain) are the only runtime needs. Same wheels both targets.
    Any failure falls back to whisper-server → whisper-cli, inherited.
    """

    provider_name = "moonshine_onnx"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.moonshine_dir = resolve_path(
            settings.get("voice.asr.moonshine_dir", "./models/moonshine/base"),
            settings.root,
        )
        self._sessions: Any | None = None
        self._session_lock = threading.Lock()
        self._broken = False

    def available(self) -> tuple[bool, str]:
        if (self.moonshine_dir / "encoder_model.onnx").is_file():
            return True, "ok (moonshine with whisper fallback)"
        return super().available()

    def _load_sessions(self) -> Any:
        with self._session_lock:
            if self._sessions is None:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                opts = ort.SessionOptions()
                encoder = ort.InferenceSession(
                    str(self.moonshine_dir / "encoder_model.onnx"),
                    opts,
                    providers=["CPUExecutionProvider"],
                )
                decoder = ort.InferenceSession(
                    str(self.moonshine_dir / "decoder_model_merged.onnx"),
                    opts,
                    providers=["CPUExecutionProvider"],
                )
                tokenizer = Tokenizer.from_file(str(self.moonshine_dir / "tokenizer.json"))
                self._sessions = (encoder, decoder, tokenizer)
            return self._sessions

    def _recognize(self, wav_path: Path) -> str:
        import numpy as np

        encoder, decoder, tokenizer = self._load_sessions()
        with wave.open(str(wav_path), "rb") as wav:
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if rate != 16000:
            # Linear resample; capture is already 16 kHz on both machines.
            duration = audio.shape[0] / float(rate)
            target = int(duration * 16000)
            audio = np.interp(
                np.linspace(0.0, audio.shape[0] - 1, target), np.arange(audio.shape[0]), audio
            ).astype(np.float32)
        seconds = audio.shape[0] / 16000.0
        (hidden,) = encoder.run(None, {"input_values": audio[None, :]})

        # Merged-decoder greedy loop (HF optimum export): first pass computes
        # the cross-attention KV, later passes reuse it via use_cache_branch.
        head_shapes: dict[str, tuple[int, int]] = {}
        for inp in decoder.get_inputs():
            if inp.name.startswith("past_key_values."):
                shape = inp.shape  # [batch, heads, seq, head_dim]
                head_shapes[inp.name] = (int(shape[1]), int(shape[3]))
        past = {
            name: np.zeros((1, heads, 0, dim), dtype=np.float32)
            for name, (heads, dim) in head_shapes.items()
        }
        tokens = [1]  # <s>
        max_tokens = max(8, int(seconds * 6.5))  # upstream length heuristic
        use_cache = np.array([False])
        input_ids = np.array([tokens], dtype=np.int64)
        for _ in range(max_tokens):
            feeds: dict[str, Any] = {
                "input_ids": input_ids,
                "encoder_hidden_states": hidden,
                "use_cache_branch": use_cache,
                **past,
            }
            outputs = decoder.run(None, feeds)
            names = [out.name for out in decoder.get_outputs()]
            by_name = dict(zip(names, outputs, strict=True))
            next_token = int(by_name["logits"][0, -1].argmax())
            tokens.append(next_token)
            if next_token == 2:  # </s>
                break
            for name in past:
                present = "present." + name.removeprefix("past_key_values.")
                fresh = by_name.get(present)
                # Cross-attention KV is only produced on the first branch;
                # keep the first-pass values thereafter.
                if fresh is not None and (".decoder." in name or not use_cache[0]):
                    past[name] = fresh
            use_cache = np.array([True])
            input_ids = np.array([[next_token]], dtype=np.int64)
        return tokenizer.decode(tokens, skip_special_tokens=True).strip()

    async def transcribe(self, wav_path: Path) -> str:
        if not wav_path.is_file():
            raise FileNotFoundError(wav_path)
        if _empty_wav(wav_path):
            return ""
        if not self._broken and (self.moonshine_dir / "encoder_model.onnx").is_file():
            try:
                text = await asyncio.to_thread(self._recognize, wav_path)
                return re.sub(r"\s+", " ", text).strip()
            except Exception as exc:
                self._broken = True
                LOG.warning(
                    "Moonshine ONNX failed (%s: %s); falling back to whisper for this session",
                    type(exc).__name__, exc,
                )
        return await super().transcribe(wav_path)


class MoonshineASR:
    """Streaming Moonshine ASR.

    Lower latency than whisper.cpp where it runs, but the published
    ``moonshine-voice`` wheels ship an arm64-only ``libmoonshine.dylib`` under a
    ``universal2`` tag and provide no Linux aarch64 build at all. It therefore
    cannot load on the Intel iMac or the Raspberry Pi 5 and must stay optional
    until upstream ships matching binaries. ``available()`` reports that up
    front instead of failing in the middle of a spoken turn.
    """

    provider_name = "moonshine"

    def __init__(self, settings: Settings):
        self.model = resolve_path(settings.require("voice.asr.model_path"), settings.root)
        self.model_arch = str(settings.get("voice.asr.model_arch", "small-streaming"))
        self._transcriber: Any | None = None
        self._load_lock = threading.Lock()
        self._transcribe_lock = asyncio.Lock()

    def available(self) -> tuple[bool, str]:
        if not self.model.is_dir():
            return False, f"Moonshine model directory not found: {self.model}"
        try:
            import moonshine_voice  # noqa: F401
        except ImportError:
            return False, "moonshine-voice is not installed (pip install -e '.[voice]')"
        library = Path(moonshine_voice.__file__).parent / "libmoonshine.dylib"
        if platform.system() == "Darwin" and platform.machine() == "x86_64" and library.exists():
            return False, (
                "The installed moonshine-voice wheel contains an arm64-only libmoonshine.dylib "
                "and cannot load on this Intel Mac. Use voice.asr.provider=whisper_cpp."
            )
        return True, "ok"

    def _load(self):
        if self._transcriber is not None:
            return self._transcriber
        ok, reason = self.available()
        if not ok:
            raise RuntimeError(reason)
        from moonshine_voice import Transcriber, string_to_model_arch

        with self._load_lock:
            if self._transcriber is None:
                self._transcriber = Transcriber(
                    str(self.model),
                    string_to_model_arch(self.model_arch),
                    update_interval=0.25,
                )
        return self._transcriber

    def _transcribe_blocking(self, wav_path: Path) -> str:
        from moonshine_voice import load_wav_file

        audio, sample_rate = load_wav_file(wav_path)
        if not audio:
            return ""
        transcript = self._load().transcribe_without_streaming(audio, sample_rate)
        text = " ".join(line.text.strip() for line in transcript.lines if line.text.strip())
        return re.sub(r"\s+", " ", text).strip()

    async def transcribe(self, wav_path: Path) -> str:
        if not wav_path.is_file():
            raise FileNotFoundError(wav_path)
        if _empty_wav(wav_path):
            return ""
        async with self._transcribe_lock:
            return await asyncio.to_thread(self._transcribe_blocking, wav_path)


def build_asr(settings: Settings):
    """Select Kendra's local ASR engine.

    Both providers are fully offline. The provider is chosen explicitly in
    configuration so a machine never silently falls back to an engine the
    operator did not qualify.
    """
    provider = str(settings.get("voice.asr.provider", "parakeet_onnx")).lower()
    if provider == "parakeet_onnx":
        return ParakeetOnnxASR(settings)
    if provider == "whisper_server":
        return WhisperServerASR(settings)
    if provider in {"whisper_cpp", "whisper", "whisper.cpp"}:
        return WhisperCppASR(settings)
    if provider == "moonshine_onnx":
        return MoonshineOnnxASR(settings)
    if provider == "moonshine":
        return MoonshineASR(settings)
    raise ValueError(
        f"Unknown voice.asr.provider: {provider!r}. "
        "Use 'parakeet_onnx', 'whisper_server', 'whisper_cpp', 'moonshine_onnx', or 'moonshine'."
    )
