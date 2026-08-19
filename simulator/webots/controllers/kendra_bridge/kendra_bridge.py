"""Virtual Kendra Webots controller.

This controller is intentionally kinematic: it visualizes Kendra's high-level
motion contract and safety behavior without pretending to be a calibrated
physics model of the final Adeept chassis. The same Body API is used by the
physical adapter later.
"""
from __future__ import annotations

import json
import math
import os
import queue
import socketserver
import threading
from dataclasses import dataclass, field
from typing import Any

from controller import Supervisor

HOST = os.environ.get("KENDRA_WEBOTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("KENDRA_WEBOTS_PORT", "8765"))
TIME_STEP = 32
PLATFORM_HALF = 1.90
CLIFF_MARGIN = 0.26
OBSTACLES = [(0.9, 0.15, 0.32)]  # x, z, conservative radius


@dataclass
class Command:
    method: str
    params: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: str | None = None


class Shared:
    def __init__(self) -> None:
        self.commands: queue.Queue[Command] = queue.Queue()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.telemetry: dict[str, Any] = {
            "ready": False,
            "front_cm": 250.0,
            "battery_voltage": 8.0,
            "cliff": {"fl": False, "fr": False, "rl": False, "rr": False},
            "pose": {"x": 0.0, "z": 0.75, "yaw": 0.0},
        }


SHARED = Shared()


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline()
        if not line:
            return
        request: dict[str, Any] = {}
        try:
            request = json.loads(line.decode("utf-8"))
            request_id = str(request.get("id", ""))
            method = str(request.get("method", ""))
            params = dict(request.get("params") or {})
            if method == "health":
                result = {"ok": True, "simulator": "webots", "bridge": "kendra_bridge", "version": 1}
            elif method == "telemetry":
                with SHARED.lock:
                    result = json.loads(json.dumps(SHARED.telemetry))
            elif method == "stop":
                SHARED.stop_event.set()
                result = {"ok": True, "stopped": True, "simulated": True}
            else:
                command = Command(method, params)
                SHARED.commands.put(command)
                if not command.done.wait(timeout=45):
                    raise TimeoutError(f"Webots command timed out: {method}")
                if command.error:
                    raise RuntimeError(command.error)
                result = command.result
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            response = {"id": str(request.get("id", "")), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_server() -> Server:
    server = Server((HOST, PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, name="kendra-webots-bridge", daemon=True)
    thread.start()
    print(f"Kendra Webots bridge listening on {HOST}:{PORT}")
    return server


class Twin:
    BASE_LEG_ANGLES = {
        "LEG_FL": -0.62,
        "LEG_ML": -1.57,
        "LEG_RL": -2.52,
        "LEG_FR": 0.62,
        "LEG_MR": 1.57,
        "LEG_RR": 2.52,
    }
    TRIPOD_A = {"LEG_FL", "LEG_MR", "LEG_RL"}

    def __init__(self, robot: Supervisor):
        self.robot = robot
        self.self_node = robot.getSelf()
        self.translation = self.self_node.getField("translation")
        self.rotation = self.self_node.getField("rotation")
        self.joints = {}
        for name in self.BASE_LEG_ANGLES:
            self.joints[name] = {
                "coxa": robot.getFromDef(f"{name}_COXA").getField("rotation"),
                "femur": robot.getFromDef(f"{name}_FEMUR").getField("rotation"),
                "tibia": robot.getFromDef(f"{name}_TIBIA").getField("rotation"),
            }
        self.head = robot.getFromDef("HEAD").getField("rotation")
        position = self.translation.getSFVec3f()
        self.x, self.y, self.z = map(float, position)
        self.yaw = 0.0
        self.head_pan = 0.0
        self.head_tilt = 0.0
        self.battery_voltage = 8.0
        self._set_neutral_legs()
        self.update_telemetry()

    def step(self, count: int = 1) -> bool:
        for _ in range(count):
            if self.robot.step(TIME_STEP) == -1:
                return False
            self.update_telemetry()
        return True

    def _set_neutral_legs(self) -> None:
        for name, base in self.BASE_LEG_ANGLES.items():
            joints = self.joints[name]
            joints["coxa"].setSFRotation([0, 1, 0, base])
            joints["femur"].setSFRotation([0, 0, 1, 0.18])
            joints["tibia"].setSFRotation([0, 0, 1, -0.72])

    def _animate_legs(self, phase: float, amplitude: float = 0.22) -> None:
        """Animate an alternating-tripod gait over 18 visual joints.

        This is a digital-twin gait visualization, not a source of real servo
        calibration values. The real Metal servo map remains a hardware gate.
        """
        for name, base in self.BASE_LEG_ANGLES.items():
            tripod_phase = phase if name in self.TRIPOD_A else phase + math.pi
            swing = math.sin(tripod_phase) * amplitude
            lift = max(0.0, math.sin(tripod_phase))
            joints = self.joints[name]
            joints["coxa"].setSFRotation([0, 1, 0, base + swing])
            joints["femur"].setSFRotation([0, 0, 1, 0.18 - (0.38 * lift)])
            joints["tibia"].setSFRotation([0, 0, 1, -0.72 + (0.48 * lift)])

    def _apply_pose(self) -> None:
        self.translation.setSFVec3f([self.x, self.y, self.z])
        self.rotation.setSFRotation([0, 1, 0, self.yaw])

    def _cliff_state(self) -> dict[str, bool]:
        # Approximate each corner sensor in world coordinates.
        sensors = {
            "fl": (0.20, -0.14),
            "fr": (0.20, 0.14),
            "rl": (-0.20, -0.14),
            "rr": (-0.20, 0.14),
        }
        state: dict[str, bool] = {}
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        for name, (lx, lz) in sensors.items():
            wx = self.x + lx * c + lz * s
            wz = self.z - lx * s + lz * c
            state[name] = abs(wx) > PLATFORM_HALF - CLIFF_MARGIN or abs(wz) > PLATFORM_HALF - CLIFF_MARGIN
        return state

    def _front_distance_cm(self) -> float:
        fx, fz = math.cos(self.yaw), -math.sin(self.yaw)
        best = 2.5
        for ox, oz, radius in OBSTACLES:
            dx, dz = ox - self.x, oz - self.z
            forward = dx * fx + dz * fz
            lateral = abs(-dx * fz + dz * fx)
            if forward > 0 and lateral < radius + 0.20:
                best = min(best, max(0.0, forward - radius))
        return best * 100.0

    def update_telemetry(self) -> None:
        self.battery_voltage = max(6.4, self.battery_voltage - 0.000002)
        with SHARED.lock:
            SHARED.telemetry = {
                "ready": True,
                "front_cm": round(self._front_distance_cm(), 2),
                "battery_voltage": round(self.battery_voltage, 3),
                "cliff": self._cliff_state(),
                "pose": {"x": round(self.x, 4), "z": round(self.z, 4), "yaw": round(self.yaw, 4)},
                "head": {"pan": round(self.head_pan, 3), "tilt": round(self.head_tilt, 3)},
            }

    def _motion_frames(self, duration: float, speed: float) -> int:
        scale = max(0.15, min(1.0, float(speed)))
        return max(6, int((duration / scale) * 1000 / TIME_STEP))

    def walk(self, direction: str, steps: int, speed: float) -> dict[str, Any]:
        if direction not in {"forward", "backward", "left", "right"}:
            raise ValueError("direction must be forward/backward/left/right")
        SHARED.stop_event.clear()
        steps = max(1, min(8, int(steps)))
        stride = 0.07
        local = {
            "forward": (stride, 0.0),
            "backward": (-stride, 0.0),
            "left": (0.0, -stride),
            "right": (0.0, stride),
        }[direction]
        for _ in range(steps):
            frames = self._motion_frames(0.32, speed)
            start_x, start_z = self.x, self.z
            c, s = math.cos(self.yaw), math.sin(self.yaw)
            world_dx = local[0] * c + local[1] * s
            world_dz = -local[0] * s + local[1] * c
            for frame in range(frames):
                if SHARED.stop_event.is_set():
                    self._set_neutral_legs()
                    return {"ok": True, "simulated": True, "stopped": True}
                t = (frame + 1) / frames
                self.x = start_x + world_dx * t
                self.z = start_z + world_dz * t
                self._animate_legs(t * math.tau)
                self._apply_pose()
                if not self.step():
                    raise RuntimeError("Webots simulation ended")
            self._set_neutral_legs()
        return {"ok": True, "simulated": True, "action": "walk", "direction": direction, "steps": steps}

    def turn(self, degrees: float, speed: float) -> dict[str, Any]:
        SHARED.stop_event.clear()
        degrees = max(-90.0, min(90.0, float(degrees)))
        target = self.yaw + math.radians(degrees)
        start = self.yaw
        frames = self._motion_frames(max(0.25, abs(degrees) / 90.0), speed)
        for frame in range(frames):
            if SHARED.stop_event.is_set():
                return {"ok": True, "simulated": True, "stopped": True}
            t = (frame + 1) / frames
            self.yaw = start + (target - start) * t
            self._animate_legs(t * math.tau, amplitude=0.16)
            self._apply_pose()
            if not self.step():
                raise RuntimeError("Webots simulation ended")
        self._set_neutral_legs()
        return {"ok": True, "simulated": True, "action": "turn", "degrees": degrees}

    def look(self, pan: float, tilt: float) -> dict[str, Any]:
        self.head_pan = max(-60.0, min(60.0, float(pan)))
        self.head_tilt = max(-10.0, min(60.0, float(tilt)))
        # Visual model has one transform, so pan is shown directly. Tilt is retained in telemetry.
        self.head.setSFRotation([0, 1, 0, math.radians(self.head_pan)])
        self.step(3)
        return {"ok": True, "simulated": True, "action": "look", "pan": self.head_pan, "tilt": self.head_tilt}

    def pose(self, name: str) -> dict[str, Any]:
        name = str(name).lower()
        if name not in {"neutral", "alert", "rest", "stretch"}:
            raise ValueError("supported virtual poses: neutral, alert, rest, stretch")
        amplitude = {"neutral": 0.0, "alert": 0.08, "rest": -0.10, "stretch": 0.16}[name]
        for leg_name, base in self.BASE_LEG_ANGLES.items():
            sign = 1.0 if leg_name in self.TRIPOD_A else -1.0
            joints = self.joints[leg_name]
            joints["coxa"].setSFRotation([0, 1, 0, base + amplitude * sign])
            if name == "rest":
                joints["femur"].setSFRotation([0, 0, 1, 0.42])
                joints["tibia"].setSFRotation([0, 0, 1, -1.02])
            elif name == "stretch":
                joints["femur"].setSFRotation([0, 0, 1, -0.04])
                joints["tibia"].setSFRotation([0, 0, 1, -0.48])
            else:
                joints["femur"].setSFRotation([0, 0, 1, 0.18])
                joints["tibia"].setSFRotation([0, 0, 1, -0.72])
        self.step(6)
        return {"ok": True, "simulated": True, "action": "pose", "name": name}

    def stop(self) -> dict[str, Any]:
        SHARED.stop_event.set()
        self._set_neutral_legs()
        return {"ok": True, "simulated": True, "stopped": True}

    def execute(self, command: Command) -> None:
        try:
            if command.method == "walk":
                command.result = self.walk(str(command.params["direction"]), int(command.params.get("steps", 1)), float(command.params.get("speed", 0.3)))
            elif command.method == "turn":
                command.result = self.turn(float(command.params["degrees"]), float(command.params.get("speed", 0.3)))
            elif command.method == "pose":
                command.result = self.pose(str(command.params.get("name", "neutral")))
            elif command.method == "look":
                command.result = self.look(float(command.params.get("pan", 0)), float(command.params.get("tilt", 20)))
            else:
                raise KeyError(f"Unknown Webots bridge method: {command.method}")
        except Exception as exc:
            command.error = f"{type(exc).__name__}: {exc}"
        finally:
            command.done.set()


def main() -> None:
    robot = Supervisor()
    twin = Twin(robot)
    server = start_server()
    try:
        while twin.step():
            try:
                while True:
                    twin.execute(SHARED.commands.get_nowait())
            except queue.Empty:
                pass
            if SHARED.stop_event.is_set():
                twin.stop()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
