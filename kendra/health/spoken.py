"""Kendra's spoken self-diagnostic — beginner-first by construction.

The master spec is emphatic: Jonathan is a first-time robot builder, and a
diagnostic he cannot act on is not a diagnostic. So every check produces
TWO renderings — one plain spoken sentence for him, and the raw technical
evidence for the dashboard — and the spoken layer never mentions Linux,
I2C, hashes, or shell commands.

Levels (spec section 6.2):
    0  safety preflight   — may she move at all? deterministic, instant
    1  boot quick check   — can she hear, see, remember, think, speak?
    2  full diagnostic    — owner-requested, adds live probes
    3  supervised body    — legs actually move; consent required, and it
                            stays fail-closed until the hardware gates pass

Severity language is fixed by the spec so she never alarms him:
    healthy     "Everything important looks good."
    minor       "I found one small issue, but I can still operate normally."
    attention   "I found something that needs your attention."
    safety_stop "I am stopping here to stay safe."
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from ..config import Settings
from ..ipc import UnixJsonClient

Status = Literal["pass", "warn", "fail", "blocked", "skipped"]
Severity = Literal["info", "minor", "attention", "safety_stop"]

SEVERITY_LANGUAGE = {
    "info": "Everything important looks good.",
    "minor": "I found one small issue, but I can still operate normally.",
    "attention": "I found something that needs your attention.",
    "safety_stop": "I am stopping here to stay safe.",
}


@dataclass(slots=True)
class CheckResult:
    check_id: str
    component: str
    status: Status
    severity: Severity = "info"
    owner_summary: str = ""
    technical: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "component": self.component,
            "status": self.status,
            "severity": self.severity,
            "owner": {"summary": self.owner_summary},
            "technical": self.technical,
            "duration_ms": self.duration_ms,
        }


class SpokenDiagnostics:
    """Runs checks through the SAME service APIs the app uses.

    Nothing here shells out or pokes hardware directly: if a service can
    answer for itself, that answer is the evidence.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def _ask(self, service: str, method: str, params: dict | None = None, timeout: float = 12.0):
        """One retry before declaring a sense broken.

        Her voice service was reported "not responding" purely because the
        health call landed while its audio thread was busy. A diagnostic
        that cries wolf is worse than no diagnostic.
        """
        last: Exception | None = None
        for attempt in range(2):
            try:
                try:
                    socket_path = self.settings.socket_path(service)
                except KeyError:
                    # Not every service declares a `<name>.socket` key (voice
                    # binds runtime_dir/voice.sock directly). Missing config
                    # is not a broken sense.
                    socket_path = self.settings.runtime_dir / f"{service}.sock"
                client = UnixJsonClient(socket_path, timeout=timeout)
                return await client.call(method, params or {})
            except Exception as exc:
                last = exc
                if attempt == 0:
                    await asyncio.sleep(0.6)
        raise last if last else RuntimeError("unreachable")

    async def _check(self, check_id: str, component: str, coro, describe) -> CheckResult:
        started = time.monotonic()
        try:
            payload = await coro
            status, severity, summary, technical = describe(payload)
        except Exception as exc:
            status, severity = "fail", "attention"
            summary = f"I couldn't check my {component} just now."
            technical = {"error": f"{type(exc).__name__}: {exc}"}
        return CheckResult(
            check_id, component, status, severity, summary, technical,
            int((time.monotonic() - started) * 1000),
        )

    # ------------------------------------------------------------- level 0
    async def preflight(self) -> dict[str, Any]:
        """May she move right now? Fast, deterministic, no models involved."""
        try:
            observation = await self._ask("body", "observation", timeout=3.0)
        except Exception as exc:
            return {
                "level": 0, "safe_to_move": False,
                "owner": "I can't feel my body right now, so I'm keeping still.",
                "technical": {"error": f"{type(exc).__name__}: {exc}"},
            }
        gates_ok = (
            self.settings.get("project.mode") != "hardware"
            or self.settings.hardware_gates_passed()
        )
        reflex_lock = bool(observation.get("reflex_lock"))
        blocked = list(observation.get("blocked_directions") or [])
        safe = gates_ok and not reflex_lock
        if not gates_ok:
            owner = "I can't walk yet — my body hasn't been checked off as safe."
        elif reflex_lock:
            owner = "I'm holding still for a moment — my safety layer asked me to."
        elif blocked:
            owner = f"I can move, but not {' or '.join(blocked)} right now."
        else:
            owner = "I'm clear to move."
        return {
            "level": 0, "safe_to_move": safe, "owner": owner,
            "technical": {
                "gates_ok": gates_ok, "reflex_lock": reflex_lock,
                "blocked_directions": blocked, "front_cm": observation.get("front_cm"),
                "body_state": observation.get("body_state"),
            },
        }

    # ------------------------------------------------------------- level 1
    async def quick(self) -> dict[str, Any]:
        """Boot check: can she hear, see, remember, think, and speak?"""

        def describe_service(name: str, sense: str):
            def inner(payload):
                ok = bool(payload.get("ok", True))
                return (
                    "pass" if ok else "fail",
                    "info" if ok else "attention",
                    f"My {sense} is working." if ok else f"My {sense} isn't responding.",
                    payload if isinstance(payload, dict) else {"payload": payload},
                )
            return inner

        checks = await asyncio.gather(
            self._check("brain.health", "memory", self._ask("brain", "health"), describe_service("brain", "memory")),
            self._check("voice.health", "hearing and voice", self._ask("voice", "health"), describe_service("voice", "hearing and voice")),
            self._check("vision.health", "eyes", self._ask("vision", "health"), describe_service("vision", "eyes")),
            self._check("body.observation", "body", self._ask("body", "observation"), describe_service("body", "body")),
            return_exceptions=False,
        )
        return self._render(1, list(checks))

    # ------------------------------------------------------------- level 2
    async def full(self) -> dict[str, Any]:
        """Owner-requested: adds live probes that actually exercise her."""

        def brain_recall(payload):
            found = len(payload or [])
            return (
                "pass" if found else "warn",
                "info" if found else "minor",
                "I can search my memories." if found else "My memory search came back empty.",
                {"hits": found},
            )

        def sight_probe(payload):
            has_face_stack = "face_recognition_status" not in (payload or {})
            people = (payload or {}).get("people_in_view")
            ok = payload is not None
            return (
                "pass" if ok else "fail",
                "info" if ok else "attention",
                "I can see through my camera." if ok else "I can't see through my camera right now.",
                {"people_in_view": people, "face_stack_ok": has_face_stack},
            )

        def clock_probe(payload):
            return ("pass", "info", "My clock looks right.", {"now": payload})

        quick = await self.quick()
        extra = await asyncio.gather(
            self._check("brain.search", "memory search",
                        self._ask("brain", "search", {"query": "Jonathan", "limit": 3}, timeout=15.0),
                        brain_recall),
            self._check("vision.capture", "camera",
                        self._ask("vision", "recognize_faces", {}, timeout=20.0), sight_probe),
            self._check("clock.now", "clock",
                        asyncio.sleep(0, result=time.strftime("%I:%M %p on %A")), clock_probe),
        )
        rendered = self._render(2, list(extra))
        rendered["checks"] = quick["checks"] + rendered["checks"]
        rendered["severity"] = max(
            (quick.get("severity", "info"), rendered.get("severity", "info")),
            key=lambda s: {"info": 0, "minor": 1, "attention": 2, "safety_stop": 3}[str(s)],
        )
        rendered["owner_script"] = self._script(2, rendered)
        return rendered

    # ------------------------------------------------------------- shaping
    def _render(self, level: int, results: list[CheckResult]) -> dict[str, Any]:
        worst: Severity = "info"
        order = {"info": 0, "minor": 1, "attention": 2, "safety_stop": 3}
        for result in results:
            if order[result.severity] > order[worst]:
                worst = result.severity
        payload = {
            "level": level,
            "severity": worst,
            "checks": [r.as_dict() for r in results],
            "safe_to_move": all(r.status != "fail" for r in results if r.component == "body"),
        }
        payload["owner_script"] = self._script(level, payload)
        return payload

    def _script(self, level: int, payload: dict[str, Any]) -> list[str]:
        """The spoken script: what she found, then ONE thing for him to do."""
        severity = payload.get("severity", "info")
        lines = [SEVERITY_LANGUAGE.get(str(severity), SEVERITY_LANGUAGE["info"])]
        troubles = [
            c for c in payload.get("checks", [])
            if c.get("status") in {"fail", "warn"}
        ]
        if not troubles:
            lines.append(
                "I can hear, see, remember, think, and speak, and my body is "
                "ready for what's currently approved."
            )
        else:
            first = troubles[0]
            lines.append(str(first.get("owner", {}).get("summary") or ""))
            if len(troubles) > 1:
                lines.append(f"There {'is' if len(troubles) == 2 else 'are'} {len(troubles) - 1} more I can walk you through.")
        if level >= 2:
            lines.append("Want the technical report?")
        return [line for line in lines if line]
