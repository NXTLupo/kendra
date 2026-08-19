from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .health.doctor import run_doctor
from .logging_setup import configure_logging


def _json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str))


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.load(getattr(args, "config", None))


def cmd_init(args: argparse.Namespace) -> int:
    settings = _settings(args)
    for key in ("paths.runtime_dir", "paths.photos_dir", "paths.outbox_dir", "paths.logs_dir", "paths.exports_dir"):
        settings.path(key).mkdir(parents=True, exist_ok=True)
    settings.path("paths.brain_db").parent.mkdir(parents=True, exist_ok=True)
    hardware_local = settings.root / "config" / "hardware.local.yaml"
    if not hardware_local.exists():
        shutil.copy2(settings.root / "config" / "hardware.example.yaml", hardware_local)
        print(f"Created safe simulation config: {hardware_local}")
    from .brain.store import BrainStore

    store = BrainStore.from_settings(settings)
    try:
        store.set_self("name", "Kendra", provenance="system")
        store.set_self("identity", "small hexapod robot and intellectual companion", provenance="system")
        store.set_self("knowledge_policy", "distinguish observed, remembered, researched, inferred, and unknown", provenance="system")
        store.set_self("network_policy", "internet is an optional research sense, never a brain dependency", provenance="system")
        stats = store.stats()
    finally:
        store.close()
    _json({"initialized": True, "brain": stats})
    return 0


def cmd_gates(args: argparse.Namespace) -> int:
    settings = _settings(args)
    gates = dict(settings.get("hardware_gates", {}))
    _json({"mode": settings.get("project.mode"), "all_passed": settings.hardware_gates_passed(), "gates": gates})
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    result = asyncio.run(run_doctor(_settings(args)))
    _json(result)
    return 0 if result["ok"] else 2


def cmd_service(args: argparse.Namespace) -> int:
    settings = _settings(args)
    name = args.name
    if name == "reflex":
        from .reflex.service import run
    elif name == "body":
        from .body.service import run
    elif name == "brain":
        from .brain.service import run
    elif name == "research":
        from .research.service import run
    elif name == "vision":
        from .vision.service import run
    elif name == "identity":
        from .identity.service import run
    elif name == "agent":
        from .agent.service import run
    elif name == "voice":
        from .voice.service import run
    elif name == "leds":
        from .leds.service import run
    elif name == "delivery":
        from .delivery.service import run
    elif name == "autonomy":
        from .autonomy.service import run
    else:
        raise ValueError(f"Unknown service: {name}")
    run(settings)
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    from .agent.service import AgentClient

    settings = _settings(args)

    async def interactive() -> None:
        client = AgentClient(settings)
        session_id = uuid.uuid4().hex
        print("Kendra local chat. Ctrl-D or Ctrl-C exits.")
        while True:
            try:
                text = await asyncio.to_thread(input, "You> ")
            except EOFError:
                break
            if not text.strip():
                continue
            result = await client.turn(text, session_id=session_id, source="terminal")
            print(f"Kendra> {result['text']}")

    try:
        asyncio.run(interactive())
    except KeyboardInterrupt:
        print()
    return 0


def _brain_direct(settings: Settings):
    from .brain.store import BrainStore

    return BrainStore.from_settings(settings)


def cmd_brain(args: argparse.Namespace) -> int:
    settings = _settings(args)
    store = _brain_direct(settings)
    try:
        if args.brain_command == "stats":
            _json(store.stats())
        elif args.brain_command == "remember":
            memory_id = store.remember(
                kind=args.kind,
                content=args.content,
                provenance=args.provenance,
                confidence=args.confidence,
                salience=args.salience,
                subject=args.subject,
                predicate=args.predicate,
                object_value=args.object,
                supersede_conflict=args.supersede,
            )
            _json({"memory_id": memory_id})
        elif args.brain_command == "search":
            _json([hit.as_dict() for hit in store.search(args.query, limit=args.limit)])
        elif args.brain_command == "correct":
            new_id = store.correct(args.memory_id, corrected_content=args.content, reason=args.reason)
            _json({"memory_id": new_id})
        elif args.brain_command == "backup":
            from .brain.backup import backup_sqlite

            _json({"path": str(backup_sqlite(store.conn, settings.path("brain.backup_dir")))})
        elif args.brain_command == "export-jsonl":
            from .brain.backup import export_jsonl, write_jsonl

            if args.stdout:
                write_jsonl(store.conn, sys.stdout)
            else:
                _json({"path": str(export_jsonl(store.conn, settings.path("brain.jsonl_export_dir")))})
        elif args.brain_command == "self":
            _json(store.self_model())
        else:
            raise ValueError("Unknown brain command")
    finally:
        store.close()
    return 0


def cmd_vision(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .ipc import UnixJsonClient

    async def go() -> Any:
        client = UnixJsonClient(settings.socket_path("vision"), timeout=120)
        if args.vision_command == "observe":
            return await client.call("observe", {"semantic": args.semantic, "question": args.question})
        if args.vision_command == "enroll":
            return await client.call("enroll_person", {"name": args.name, "frames": args.frames, "consent": args.consent, "relationship": args.relationship})
        if args.vision_command == "recognize":
            return await client.call("recognize_faces")
        raise ValueError("Unknown vision command")

    _json(asyncio.run(go()))
    return 0


def cmd_voice_console(args: argparse.Namespace) -> int:
    from .voice.service import voice_console

    try:
        asyncio.run(voice_console(_settings(args)))
    except KeyboardInterrupt:
        print()
    return 0



def cmd_dev(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .devstack import DevStack

    config_value = getattr(args, "config", None) or os.getenv("KENDRA_CONFIG") or "config/pc.yaml"
    config_path = Path(config_value)
    if not config_path.is_absolute():
        config_path = settings.root / config_path
    stack = DevStack(settings, config_path)
    if args.dev_command == "start":
        _json(stack.start(include_voice=args.voice, include_autonomy=args.autonomy))
    elif args.dev_command == "stop":
        _json(stack.stop())
    elif args.dev_command == "status":
        _json(stack.status())
    else:
        raise ValueError("Unknown dev command")
    return 0


def cmd_update_verify(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .updates.verify import UpdateVerifier

    verifier = UpdateVerifier(settings.path("updates.public_key_file"))
    manifest = Path(args.manifest).expanduser().resolve()
    signature = Path(args.signature).expanduser().resolve()
    verifier.verify_manifest(manifest, signature)
    result = verifier.verify_artifacts(manifest, manifest.parent)
    _json({"signature": "valid", **result})
    return 0


def cmd_dashboard_bridge(args: argparse.Namespace) -> int:
    from .dashboard.bridge import run

    run(_settings(args))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kendra", description="Kendra onboard robot runtime")
    parser.add_argument("--config", help="Optional YAML overlay path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Initialize local directories and Kendra Brain")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("gates", help="Show hard hardware gates")
    p.set_defaults(func=cmd_gates)
    p = sub.add_parser("doctor", help="Check local runtime prerequisites")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("service", help="Run one Kendra service")
    p.add_argument("name", choices=["reflex", "body", "brain", "identity", "research", "vision", "agent", "voice", "leds", "delivery", "autonomy"])
    p.set_defaults(func=cmd_service)

    p = sub.add_parser("chat", help="Interactive local text chat through the agent service")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("voice-console", help="Push-to-listen local voice console")
    p.set_defaults(func=cmd_voice_console)

    p = sub.add_parser("dashboard-bridge", help="Run the native desktop stdio bridge")
    p.set_defaults(func=cmd_dashboard_bridge)

    brain = sub.add_parser("brain", help="Inspect and maintain Kendra Brain")
    brain_sub = brain.add_subparsers(dest="brain_command", required=True)
    b = brain_sub.add_parser("stats")
    b.set_defaults(func=cmd_brain)
    b = brain_sub.add_parser("remember")
    b.add_argument("content")
    b.add_argument("--kind", default="fact")
    b.add_argument("--provenance", choices=["observed", "user_stated", "researched", "inferred", "system"], default="user_stated")
    b.add_argument("--confidence", type=float, default=0.9)
    b.add_argument("--salience", type=float, default=0.6)
    b.add_argument("--subject")
    b.add_argument("--predicate")
    b.add_argument("--object")
    b.add_argument("--supersede", action="store_true")
    b.set_defaults(func=cmd_brain)
    b = brain_sub.add_parser("search")
    b.add_argument("query")
    b.add_argument("--limit", type=int, default=10)
    b.set_defaults(func=cmd_brain)
    b = brain_sub.add_parser("correct")
    b.add_argument("memory_id", type=int)
    b.add_argument("content")
    b.add_argument("--reason")
    b.set_defaults(func=cmd_brain)
    b = brain_sub.add_parser("backup")
    b.set_defaults(func=cmd_brain)
    b = brain_sub.add_parser("export-jsonl")
    b.add_argument("--stdout", action="store_true", help="Stream the export for a trusted SSH transfer")
    b.set_defaults(func=cmd_brain)
    b = brain_sub.add_parser("self")
    b.set_defaults(func=cmd_brain)

    vision = sub.add_parser("vision", help="Vision service operations")
    vision_sub = vision.add_subparsers(dest="vision_command", required=True)
    v = vision_sub.add_parser("observe")
    v.add_argument("--semantic", action="store_true")
    v.add_argument("--question", default="Describe the scene briefly.")
    v.set_defaults(func=cmd_vision)
    v = vision_sub.add_parser("enroll")
    v.add_argument("name")
    v.add_argument("--frames", type=int, default=8)
    v.add_argument("--relationship")
    v.add_argument("--consent", action="store_true", help="Confirm the person explicitly consented to local biometric enrollment")
    v.set_defaults(func=cmd_vision)
    v = vision_sub.add_parser("recognize")
    v.set_defaults(func=cmd_vision)

    dev = sub.add_parser("dev", help="Manage the cross-platform Virtual Kendra development stack")
    dev_sub = dev.add_subparsers(dest="dev_command", required=True)
    d = dev_sub.add_parser("start", help="Start local Virtual Kendra services")
    d.add_argument("--voice", action="store_true", help="Also start the local voice service")
    d.add_argument("--autonomy", action="store_true", help="Also start autonomy (disabled by default)")
    d.set_defaults(func=cmd_dev)
    d = dev_sub.add_parser("stop", help="Stop local Virtual Kendra services")
    d.set_defaults(func=cmd_dev)
    d = dev_sub.add_parser("status", help="Show local Virtual Kendra service status")
    d.set_defaults(func=cmd_dev)

    update = sub.add_parser("update-verify", help="Verify a signed update manifest and artifacts")
    update.add_argument("manifest")
    update.add_argument("signature")
    update.set_defaults(func=cmd_update_verify)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = _settings(args)
    configure_logging(str(settings.get("logging.level", "INFO")))
    try:
        code = int(args.func(args))
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)
