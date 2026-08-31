"""Does the system that booted match the system that was designed?

Kendra's most expensive defect class is not broken logic — it is a component
swapped in one place and left stale everywhere else. On 2026-08-22 her brain
had been serving the *merged* fine-tune with activation steering at scale 2.0
for two days: a configuration three files in this repository say not to use,
and which both defaults in ``scripts/start_llm_intel_macos.sh`` disable. Every
health check passed the whole time, because they asked whether the port
answered rather than what was behind it.

Liveness is not truth. This module asks what is actually loaded and compares it
against the declared configuration, so drift fails a check instead of quietly
producing a worse Kendra.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings

LOG = logging.getLogger(__name__)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


async def llm_runtime_facts(base_url: str, timeout: float = 4.0) -> dict[str, Any]:
    """What the local model server says about itself, or an error."""
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{root}/props")
            response.raise_for_status()
            props = response.json()
    except Exception as exc:
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}
    generation = props.get("default_generation_settings") or {}
    return {
        "reachable": True,
        "model_path": str(props.get("model_path") or ""),
        "n_ctx": generation.get("n_ctx"),
    }


def _check_model(facts: dict[str, Any], settings: Settings) -> dict[str, Any]:
    expected = _as_list(settings.get("llm.expect.model_path_contains"))
    forbidden = _as_list(settings.get("llm.expect.model_path_forbids"))
    loaded = str(facts.get("model_path") or "")
    problems: list[str] = []
    if expected and not any(token in loaded for token in expected):
        problems.append(
            f"loaded model {loaded!r} matches none of the expected markers {expected}"
        )
    for token in forbidden:
        if token and token in loaded:
            problems.append(f"loaded model {loaded!r} contains forbidden marker {token!r}")
    return {"ok": not problems, "loaded": loaded, "problems": problems}


def check_llm_process(settings: Settings) -> dict[str, Any]:
    """Inspect the model server's *command line*, which is where drift hides.

    ``/props`` reports the model file but says nothing about a LoRA adapter or
    a control vector, and those are exactly the flags that were wrong. The
    argv of the running process is the only place they are visible.
    """
    import subprocess

    result: dict[str, Any] = {"ok": True, "problems": [], "argv": None}
    try:
        completed = subprocess.run(
            ["ps", "-Ao", "args="], capture_output=True, text=True, timeout=5, check=False
        )
    except Exception as exc:  # pragma: no cover - platform dependent
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    port = str(settings.get("llm.base_url", "")).rsplit(":", 1)[-1].split("/")[0]
    argv = next(
        (
            line.strip()
            for line in completed.stdout.splitlines()
            if "llama-server" in line and f"--port {port}" in line
        ),
        None,
    )
    if argv is None:
        result["ok"] = False
        result["problems"].append(f"no llama-server found serving port {port}")
        return result
    result["argv"] = argv

    steering_allowed = bool(settings.get("llm.expect.allow_control_vector", False))
    if "--control-vector" in argv and not steering_allowed:
        result["problems"].append(
            "activation steering (--control-vector) is active, but the recorded "
            "decision is that it ships OFF: it added nothing over the fine-tune "
            "and cost reasoning (docs/CONSCIOUSNESS_VECTOR.md)"
        )
    required_lora = str(settings.get("llm.expect.require_lora") or "")
    if required_lora and required_lora not in argv:
        result["problems"].append(
            f"expected the LoRA adapter {required_lora!r} to be applied, and it is not"
        )
    result["ok"] = not result["problems"]
    return result


def check_embeddings(settings: Settings) -> dict[str, Any]:
    """The provider in force versus the provider the documentation claims."""
    active = str(settings.get("brain.embedding.provider", "hashing"))
    declared = _as_list(settings.get("brain.embedding.expect_provider")) or [active]
    ok = active in declared
    problems = []
    if not ok:
        problems.append(
            f"embedding provider is {active!r} but the profile declares {declared}"
        )
    if active == "hashing":
        problems.append(
            "embedding provider is 'hashing' — a bag-of-words hash, not semantic "
            "memory. Measured on her real corpus this costs little (recall@4 7/12 "
            "either way), but nothing may describe it as semantic while it is set."
        )
    return {"ok": ok, "provider": active, "declared": declared, "problems": problems}


def check_service_freshness(settings: Settings) -> dict[str, Any]:
    """Is each running service still running the code it loaded?

    Answered per service, from what that service itself imported, not by
    scanning the package. The first version of this compared every service
    against the newest file anywhere under ``kendra/`` -- so editing the
    vision service reported all ten as stale, which is a smoke alarm that
    goes off when you make toast. Precision is what makes it worth reading.

    Each service records its own loaded source files at boot (see
    ``kendra.codestamp``) and exits when those exact files change, so in
    normal operation this should always be empty. A non-empty result means
    something is preventing that: the watchdog is disabled, or a service is
    wedged and not restarting.
    """
    import subprocess

    from ..codestamp import service_report

    result: dict[str, Any] = {"ok": True, "problems": [], "stale": []}
    known = {
        "reflex", "body", "brain", "identity", "research", "vision",
        "agent", "voice", "leds", "delivery", "autonomy",
    }
    try:
        completed = subprocess.run(
            ["ps", "-Ao", "args="], capture_output=True, text=True, timeout=5, check=False
        )
    except Exception:  # pragma: no cover - platform dependent
        return result
    running: dict[str, int] = {}
    for line in completed.stdout.splitlines():
        if "-m kendra" not in line or " service " not in line:
            continue
        name = line.rsplit(" service ", 1)[-1].split()[0]
        if name in known:
            running[name] = running.get(name, 0)

    # Match each service to its pid so a leftover stamp from a dead process
    # cannot vouch for a live one.
    try:
        with_pids = subprocess.run(
            ["ps", "-Ao", "pid=,args="], capture_output=True, text=True, timeout=5, check=False
        ).stdout
        for line in with_pids.splitlines():
            if "-m kendra" not in line or " service " not in line:
                continue
            pid, _, rest = line.strip().partition(" ")
            name = rest.rsplit(" service ", 1)[-1].split()[0]
            if name in running and pid.isdigit():
                running[name] = int(pid)
    except Exception:  # pragma: no cover - platform dependent
        pass

    unknown: list[str] = []
    for name in sorted(running):
        report = service_report(settings.runtime_dir, name, running[name] or None)
        if report["state"] == "stale":
            result["stale"].append(
                {"service": name, "files": sorted(Path(f).name for f in report["changed"])[:8]}
            )
        elif report["state"] == "unknown":
            unknown.append(name)

    if result["stale"]:
        detail = "; ".join(
            f"{item['service']} ({', '.join(item['files'])})" for item in result["stale"]
        )
        result["problems"].append(
            f"these services are running code that has since changed: {detail}. "
            "They should have exited and been restarted on their own — check "
            "dev.exit_on_stale_code and the desktop supervisor."
        )
        result["ok"] = False
    if unknown:
        # Not "fine". A service that cannot say what it loaded is exactly the
        # situation that let the whole stack run the previous evening's code
        # for a day while every check reported health.
        result["unknown"] = unknown
        result["problems"].append(
            f"these services cannot say what code they are running: {', '.join(unknown)}. "
            "Restart the stack so they record a stamp."
        )
        result["ok"] = False
    return result


def check_self_knowledge(settings: Settings) -> dict[str, Any]:
    """Do her stored architecture memories still match the spec on disk?

    She recites these as fact about herself. Asked whether her memory had
    improved, she answered "My current memory includes Qwen3-Embedding-0.6B
    semantic vectors" -- a stored snapshot of a spec that had already been
    corrected on disk, describing an embedding provider she has never run.

    A stale line here is not a documentation problem. It is a false statement
    she makes confidently, in her own voice, about her own body.
    """
    result: dict[str, Any] = {"ok": True, "problems": [], "stale": []}
    doc = settings.root / "docs/ARCHITECTURE_CURRENT.md"
    try:
        current = {
            match.group(1): match.group(2)
            for line in doc.read_text(encoding="utf-8").splitlines()
            if (match := re.match(r"^- (\w[\w -]*?): (.+)$", line.strip()))
        }
    except OSError:
        return result
    if not current:
        return result
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{settings.path('paths.brain_db')}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT predicate, content FROM memories "
            "WHERE active=1 AND subject='architecture' AND predicate LIKE 'component:%'"
        ).fetchall()
        conn.close()
    except Exception:
        return result
    for predicate, content in rows:
        component = str(predicate).split(":", 1)[-1]
        expected = current.get(component)
        if expected is None:
            continue
        if expected[:120] not in str(content):
            result["stale"].append(component)
    if result["stale"]:
        result["ok"] = False
        result["problems"].append(
            "her stored self-knowledge disagrees with docs/ARCHITECTURE_CURRENT.md "
            f"for: {', '.join(sorted(result['stale']))}. She will state the stale "
            "version as fact about herself. Fix: "
            "python scripts/sync_architecture_memory.py --config <profile>"
        )
    return result


async def verify_runtime(settings: Settings) -> dict[str, Any]:
    """Full truth report. ``ok`` is False on any drift from the declaration."""
    facts = await llm_runtime_facts(str(settings.require("llm.base_url")))
    report: dict[str, Any] = {"llm_runtime": facts}
    if facts.get("reachable"):
        report["llm_model"] = _check_model(facts, settings)
    else:
        report["llm_model"] = {"ok": False, "problems": ["model server unreachable"]}
    report["llm_process"] = check_llm_process(settings)
    report["embeddings"] = check_embeddings(settings)
    report["service_freshness"] = check_service_freshness(settings)
    report["self_knowledge"] = check_self_knowledge(settings)
    problems: list[str] = []
    for name, section in report.items():
        if isinstance(section, dict):
            problems.extend(f"{name}: {problem}" for problem in section.get("problems", []))
    report["ok"] = not problems
    report["problems"] = problems
    return report


async def warn_on_drift(settings: Settings) -> dict[str, Any]:
    """Log drift loudly at service start. Never raises, never blocks a boot.

    Refusing to start would leave Jonathan with no Kendra at all over a
    configuration difference, which is worse than a degraded one. A loud,
    specific warning at every boot is the right severity.
    """
    try:
        report = await verify_runtime(settings)
    except Exception as exc:
        LOG.debug("Runtime truth check unavailable: %s", exc)
        return {"ok": True, "problems": [], "skipped": True}
    for problem in report.get("problems", []):
        LOG.warning("RUNTIME DRIFT — %s", problem)
    if report.get("problems"):
        LOG.warning(
            "The running system does not match the declared one. "
            "Run: python -m kendra --config <profile> truth"
        )
    return report
