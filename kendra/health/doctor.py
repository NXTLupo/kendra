from __future__ import annotations

import shutil
import sys
from typing import Any

import httpx

from ..config import Settings
from ..ipc import UnixJsonClient
from ..paths import resolve_path


async def _http_ok(url: str) -> tuple[bool, int | None]:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(url)
            return 200 <= response.status_code < 300, response.status_code
    except Exception:
        return False, None


async def run_doctor(settings: Settings) -> dict[str, Any]:
    """Return install readiness and live-service diagnostics separately.

    ``ok`` means the selected profile has the files/processes required to start
    the core local interaction loop. Services that have not been launched yet
    are reported but do not make installation readiness ambiguous.
    """

    checks: dict[str, Any] = {}
    checks["python"] = {"ok": sys.version_info >= (3, 11), "version": sys.version.split()[0]}
    checks["mode"] = {"ok": True, "value": settings.get("project.mode")}
    gates = dict(settings.get("hardware_gates", {}))
    checks["hardware_gates"] = {
        "ok": settings.get("project.mode") != "hardware" or settings.hardware_gates_passed(),
        "gates": gates,
    }

    for command in ("git", "ffmpeg", "minisign", "docker"):
        path = shutil.which(command)
        checks[f"bin:{command}"] = {"ok": path is not None, "path": path}

    # Check the ASR engine that is actually selected, and check that it can
    # really load. A present model directory is not proof of a working engine:
    # the moonshine wheel ships an arm64-only library that passes every file
    # check and then fails at the first spoken word.
    try:
        from ..voice.asr import build_asr

        asr = build_asr(settings)
        ready, detail = asr.available()
        checks["asr"] = {"ok": ready, "provider": asr.provider_name, "detail": detail}
    except Exception as exc:
        checks["asr"] = {"ok": False, "provider": settings.get("voice.asr.provider"), "detail": f"{type(exc).__name__}: {exc}"}

    files = {
        "charter": settings.path("paths.charter"),
        "piper_model": settings.path("voice.tts.model"),
        "vosk_model": resolve_path(settings.require("voice.wake.vosk_model"), settings.root),
        "yunet_model": settings.path("vision.face.yunet_model"),
        "sface_model": settings.path("vision.face.sface_model"),
    }
    for name, path in files.items():
        checks[f"file:{name}"] = {"ok": path.exists(), "path": str(path)}

    llm_health = str(settings.require("llm.base_url")).removesuffix("/v1") + "/health"
    ok, status = await _http_ok(llm_health)
    checks["llm_http"] = {"ok": ok, "status": status, "url": llm_health}

    vlm_url = settings.get("vision.semantic_vlm_url")
    if vlm_url:
        vlm_health = str(vlm_url).removesuffix("/v1") + "/health"
        ok, status = await _http_ok(vlm_health)
        checks["vlm_http"] = {"ok": ok, "status": status, "url": vlm_health}

    for key, setting in (("searxng_http", "research.searxng_url"), ("kiwix_http", "research.kiwix_url")):
        url = str(settings.require(setting))
        ok, status = await _http_ok(url)
        checks[key] = {"ok": ok, "status": status, "url": url, "optional_for_offline_chat": True}

    socket_sections = ["reflex", "body", "brain", "identity", "research", "vision", "agent", "autonomy", "leds"]
    running_count = 0
    for section in socket_sections:
        try:
            client = UnixJsonClient(settings.socket_path(section), timeout=0.5)
            result = await client.call("health")
            healthy = bool(result.get("ok", True))
            checks[f"service:{section}"] = {"ok": healthy, "result": result}
            running_count += int(healthy)
        except Exception as exc:
            checks[f"service:{section}"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    for name, socket in {"voice": settings.runtime_dir / "voice.sock", "delivery": settings.runtime_dir / "delivery.sock"}.items():
        try:
            result = await UnixJsonClient(socket, timeout=0.5).call("health")
            healthy = bool(result.get("ok", True))
            checks[f"service:{name}"] = {"ok": healthy, "result": result}
            running_count += int(healthy)
        except Exception as exc:
            checks[f"service:{name}"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    required = [
        "python",
        "hardware_gates",
        "file:charter",
        "asr",
        "file:piper_model",
        "file:vosk_model",
        "file:yunet_model",
        "file:sface_model",
        "llm_http",
    ]
    # The semantic VLM is opt-in (2.6 GB resident; see EDGE_MODEL_PLAN.md).
    # Its absence must not fail install readiness; the check above still
    # reports its live state for the dashboard.
    install_ready = all(bool(checks[key]["ok"]) for key in required)
    core_service_keys = [
        f"service:{name}"
        for name in (
            "reflex",
            "body",
            "brain",
            "identity",
            "research",
            "vision",
            "leds",
            "delivery",
            "agent",
        )
    ]
    live_stack_ready = all(bool(checks[key]["ok"]) for key in core_service_keys)
    return {
        "ok": install_ready,
        "install_ready": install_ready,
        "live_stack_ready": live_stack_ready,
        "healthy_service_count": running_count,
        "required_checks": required,
        "checks": checks,
    }
