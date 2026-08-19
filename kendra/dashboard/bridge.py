from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..agent.service import AgentClient
from ..brain.service import BrainClient
from ..brain.sync import BrainSyncClient
from ..config import Settings
from ..ipc import UnixJsonClient
from ..updates.git import GitUpdateInspector
from ..updates.installer import SignedReleaseStager
from ..vision.service import VisionClient
from ..voice.acks import AckPlayer
from ..voice.asr import build_asr
from ..voice.tts import PiperTTS
from .controller import DashboardController

LOG = logging.getLogger(__name__)

ALLOWED_BODY_COMMANDS = {"walk", "turn", "look", "pose", "stop"}
ALLOWED_AUDIO_TYPES = {"audio/webm", "audio/mp4", "audio/ogg", "audio/wav", "audio/x-wav"}


class DashboardBridge:
    """Named desktop commands carried over stdio, never a network API."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.controller = DashboardController(settings)
        self.asr = build_asr(settings)
        self.tts = PiperTTS(settings)
        self.acks = AckPlayer(settings, self.tts)

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "snapshot":
            return await self.controller.snapshot()
        if method == "chat":
            if str(self.settings.get("project.interaction_mode", "voice_first")) == "voice_first":
                raise PermissionError(
                    "Text chat is disabled in voice-first mode — talk to Kendra instead"
                )
            text = str(params.get("text") or "").strip()
            if not text or len(text) > 2_000:
                raise ValueError("Chat text must contain 1-2,000 characters")
            return await AgentClient(self.settings).turn(text, source="desktop")
        if method == "voice_begin":
            return await UnixJsonClient(self.settings.runtime_dir / "voice.sock", timeout=3).call(
                "desktop_capture_begin"
            )
        if method == "voice_end":
            return await UnixJsonClient(self.settings.runtime_dir / "voice.sock", timeout=3).call(
                "desktop_capture_end"
            )
        if method == "listen":
            # Hands-free turn using Kendra's own microphone capture: energy VAD
            # detects the pause at the end of the utterance and stops on its
            # own. This is the identical code path the robot body uses after
            # the wake phrase, so nothing here depends on a button that will
            # not exist once she is on the Pi.
            timeout = float(self.settings.get("agent.client_timeout_seconds", 320))
            return await UnixJsonClient(
                self.settings.runtime_dir / "voice.sock", timeout=timeout
            ).call("listen_once")
        if method == "voice_audio":
            try:
                return await self._voice_audio(params)
            finally:
                try:
                    await UnixJsonClient(self.settings.runtime_dir / "voice.sock", timeout=3).call(
                        "desktop_capture_end"
                    )
                except Exception:
                    pass
        if method == "vision_frame":
            encoded = str(params.get("image") or "")
            if not encoded or len(encoded) > 12 * 1024 * 1024:
                raise ValueError("Camera frame is missing or too large")
            return await VisionClient(self.settings).submit_frame(encoded)
        if method == "observe":
            result = await VisionClient(self.settings).observe(
                bool(params.get("semantic", True)),
                str(params.get("question") or "Describe what you see briefly.")[:400],
            )
            await self.controller.record_event(
                "dashboard_camera_observation",
                {"semantic": bool(params.get("semantic", True)), "photo_id": result.get("photo_id")},
            )
            return result
        if method == "body":
            command = str(params.get("command") or "")
            if command not in ALLOWED_BODY_COMMANDS:
                raise ValueError("Unsupported body command")
            values = params.get("params") if isinstance(params.get("params"), dict) else {}
            result = await UnixJsonClient(self.settings.socket_path("body"), timeout=12).call(command, values)
            await self.controller.record_event("dashboard_body_command", {"command": command, "params": values})
            return result
        if method == "memories":
            query = str(params.get("query") or "")[:500]
            limit = min(50, max(1, int(params.get("limit", 20))))
            return {"memories": await BrainClient(self.settings).search(query, limit)}
        if method == "memory_import":
            encoded = str(params.get("data") or "")
            if len(encoded) > (self.controller.upload_limit * 4 // 3) + 8:
                raise ValueError("Brain transfer exceeds the configured safety limit")
            try:
                body = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise ValueError("Brain transfer is not valid base64") from exc
            return await self.controller.import_upload(body, str(params.get("filename") or "kendra-brain.jsonl"))
        if method == "memory_sync":
            return await BrainSyncClient(self.settings).pull(
                str(params.get("host") or "") or None,
                str(params.get("user") or "") or None,
            )
        if method == "memory_backup":
            return await BrainClient(self.settings).rpc.call("backup")
        if method == "update_check":
            inspector = GitUpdateInspector(self.settings)
            result = await asyncio.to_thread(inspector.status, fetch=True)
            await self.controller.record_event(
                "intelligence_update_checked",
                {"upgrade_available": result.get("upgrade_available", False)},
            )
            return result
        if method == "update_request":
            confirmation = str(params.get("confirmation") or "").strip().lower()
            if confirmation != "install signed intelligence upgrade":
                raise PermissionError("Exact signed-upgrade confirmation phrase is required")
            return await asyncio.to_thread(SignedReleaseStager(self.settings).stage)
        if method == "photo":
            return {"data_url": self.controller.photo_data_url(str(params.get("name") or ""))}
        raise KeyError(f"Unknown desktop command: {method}")

    async def _voice_audio(self, params: dict[str, Any]) -> dict[str, Any]:
        mime = str(params.get("mime") or "").split(";", 1)[0].lower()
        if mime not in ALLOWED_AUDIO_TYPES:
            raise ValueError("Unsupported microphone recording type")
        encoded = str(params.get("data") or "")
        if len(encoded) > 40 * 1024 * 1024:
            raise ValueError("Microphone recording is too large")
        try:
            audio = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("Microphone recording is not valid base64") from exc
        if not audio:
            return {"heard": "", "response": ""}
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise FileNotFoundError("ffmpeg is required for the desktop microphone bridge")
        suffix = {"audio/webm": ".webm", "audio/mp4": ".m4a", "audio/ogg": ".ogg"}.get(mime, ".wav")
        with tempfile.TemporaryDirectory(prefix="kendra-desktop-voice-") as directory:
            # The recording and the 16 kHz mono conversion must never be the same
            # path: when the renderer hands us audio/wav, ffmpeg refuses to run
            # with "Output ... same as Input" and every spoken turn fails.
            source = Path(directory) / f"recording{suffix}"
            wav = Path(directory) / "converted-16k-mono.wav"
            source.write_bytes(audio)
            process = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(wav),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"Could not decode the microphone recording: {stderr.decode(errors='replace')[-800:]}")
            # She heard you: acknowledge instantly while ASR and the LLM run.
            # prepare() only synthesizes clips that are missing, so this is a
            # handful of stat() calls on every turn after the first.
            await self.acks.prepare()
            self.acks.play_random()
            heard = await self.asr.transcribe(wav)
        # Log the transcript. Without it there is no way to tell a failed
        # recording from a mis-heard one from a bad answer, and "she makes no
        # sense" is unfixable. Local-only, on the owner's own machine.
        LOG.info("desktop voice heard: %r", heard)
        if not heard:
            LOG.warning("desktop voice produced an empty transcript from %d bytes of audio", len(audio))
            return {"heard": "", "response": ""}
        if heard.strip().lower() in {"stop", "kendra stop", "stop kendra"}:
            await UnixJsonClient(self.settings.socket_path("body"), timeout=2).call(
                "stop", {"reason": "spoken desktop stop"}
            )
            return {"heard": heard, "response": "Stopped.", "affect": "alert"}
        result = await AgentClient(self.settings).turn(heard, source="desktop-voice")
        response = str(result.get("text") or "")
        affect = str(result.get("affect") or "warm")
        await self.tts.speak(response, affect=affect)
        return {"heard": heard, "response": response, "affect": affect, "session_id": result.get("session_id")}


async def _serve(settings: Settings) -> None:
    bridge = DashboardBridge(settings)
    write_lock = asyncio.Lock()
    tasks: set[asyncio.Task[None]] = set()

    async def emit(value: dict[str, Any]) -> None:
        async with write_lock:
            sys.stdout.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()

    async def handle(line: str) -> None:
        request_id: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Desktop IPC request must be an object")
            request_id = request.get("id")
            method = str(request.get("method") or "")
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            result = await bridge.dispatch(method, params)
            await emit({"id": request_id, "ok": True, "result": result})
        except Exception as exc:
            await emit({"id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            value = line.strip()
            if value:
                task = asyncio.create_task(handle(value))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
    finally:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def run(settings: Settings) -> None:
    asyncio.run(_serve(settings))
