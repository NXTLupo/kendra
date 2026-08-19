from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from ..brain.service import BrainClient
from ..config import Settings
from ..connectivity import assert_loopback_http_url
from ..identity.service import IdentityClient
from ..ipc import UnixJsonClient, UnixJsonServer

LOG = logging.getLogger(__name__)


class VisionService:
    DEFAULT_PERSPECTIVE = (
        "You are Kendra, a robot companion, looking through your own camera. "
        "Describe only what is actually visible, plainly and accurately. If you "
        "are unsure about something, say you are unsure instead of guessing."
    )

    def __init__(self, settings: Settings):
        self.settings = settings
        self.server = UnixJsonServer(settings.socket_path("vision"), self.handle)
        self.photos_dir = settings.path("paths.photos_dir")
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        self.identity = IdentityClient(settings)
        self.brain = BrainClient(settings)
        semantic_endpoint = settings.get("vision.semantic_vlm_url")
        if semantic_endpoint:
            assert_loopback_http_url(str(semantic_endpoint))

    def _cv2(self):
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("Install the vision extra: pip install -e '.[vision]'") from exc
        return cv2

    def _capture_opencv(self) -> Any:
        cv2 = self._cv2()
        camera_index = int(self.settings.get("vision.camera_index", 0))
        cap = cv2.VideoCapture(camera_index)
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.settings.get("vision.frame_width", 1280)))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.settings.get("vision.frame_height", 720)))
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError("Camera did not return a frame")
            return frame
        finally:
            cap.release()

    def _capture_picamera2(self) -> Any:
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise RuntimeError("Picamera2 is not installed. On Raspberry Pi OS install python3-picamera2.") from exc
        width = int(self.settings.get("vision.frame_width", 1280))
        height = int(self.settings.get("vision.frame_height", 720))
        camera = Picamera2()
        try:
            config = camera.create_still_configuration(main={"size": (width, height), "format": "RGB888"})
            camera.configure(config)
            camera.start()
            time.sleep(0.25)
            rgb = camera.capture_array()
        finally:
            camera.stop()
            camera.close()
        cv2 = self._cv2()
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    def submit_frame(self, image_b64: str) -> dict[str, Any]:
        """Accept a camera frame pushed by a trusted local frontend.

        macOS grants webcam access to the desktop app, never to this headless
        service, so the Electron renderer streams frames here. On the robot no
        frontend pushes frames and capture() uses the Pi camera directly —
        identical code, different eye.
        """
        import base64

        cv2 = self._cv2()
        import numpy as np

        raw = base64.b64decode(image_b64, validate=True)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Provided frame is too large")
        frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Provided frame could not be decoded")
        # Motion signal for ambient vision (Penguin-VL's keyframe insight,
        # inverted: spend VLM cycles only when the world actually changes).
        try:
            small = cv2.cvtColor(cv2.resize(frame, (96, 54)), cv2.COLOR_BGR2GRAY).astype("float32")
            previous = getattr(self, "_motion_reference", None)
            if previous is not None:
                score = float(abs(small - previous).mean())
                if score >= float(self.settings.get("vision.ambient.motion_threshold", 10.0)):
                    self._motion_pending = True
                    self._last_motion_at = time.time()
            self._motion_reference = small
        except Exception:
            pass
        self._provided_frame = frame
        self._provided_frame_at = time.time()
        return {"ok": True, "shape": list(frame.shape)}

    def capture(self) -> tuple[Any, Path]:
        provider = str(self.settings.get("vision.camera_provider", "opencv"))
        max_age = float(self.settings.get("vision.provided_frame_max_age_seconds", 12.0))
        frame = None
        provided_at = getattr(self, "_provided_frame_at", 0.0)
        if getattr(self, "_provided_frame", None) is not None and time.time() - provided_at <= max_age:
            frame = self._provided_frame
        else:
            try:
                if provider == "picamera2":
                    frame = self._capture_picamera2()
                elif provider == "opencv":
                    frame = self._capture_opencv()
                else:
                    raise ValueError(f"Unknown camera provider: {provider}")
            except Exception:
                # The direct camera is dead (typical on macOS, where the webcam
                # belongs to the desktop app). The renderer streams a frame
                # every ~5s, so wait one beat for a fresh one, then accept a
                # slightly stale one over blindness. The Pi, with its own
                # camera and no frame provider, still raises normally.
                deadline = time.time() + 6.0
                while time.time() < deadline:
                    if getattr(self, "_provided_frame", None) is not None and time.time() - getattr(self, "_provided_frame_at", 0.0) <= max_age:
                        frame = self._provided_frame
                        break
                    time.sleep(0.25)
                if frame is None and getattr(self, "_provided_frame", None) is not None and time.time() - provided_at <= 20.0:
                    frame = self._provided_frame
                if frame is None:
                    raise
        cv2 = self._cv2()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.photos_dir / f"kendra-{stamp}-{time.time_ns() % 1000000:06d}.jpg"
        if not cv2.imwrite(str(path), frame):
            raise RuntimeError("Could not write captured JPEG")
        return frame, path

    def detect_people(self, frame: Any) -> int:
        try:
            detector, _ = self._face_models()
            height, width = frame.shape[:2]
            detector.setInputSize((width, height))
            _, faces = detector.detect(frame)
            return 0 if faces is None else int(len(faces))
        except FileNotFoundError:
            # Fallback only: the HOG pedestrian detector false-positives
            # wildly on indoor scenes (it once counted a guitar as people).
            cv2 = self._cv2()
            hog = cv2.HOGDescriptor()
            hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            boxes, _ = hog.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
            return int(len(boxes))

    def detect_perch(self, frame: Any) -> dict[str, Any]:
        cv2 = self._cv2()
        if not hasattr(cv2, "aruco"):
            return {"found": False, "reason": "opencv aruco module unavailable"}
        dictionary_name = str(self.settings.get("vision.aruco_dictionary", "DICT_4X4_50"))
        dictionary_id = getattr(cv2.aruco, dictionary_name)
        dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        detector = cv2.aruco.ArucoDetector(dictionary)
        corners, ids, _ = detector.detectMarkers(frame)
        target = int(self.settings.get("vision.perch_marker_id", 23))
        if ids is None:
            return {"found": False}
        flat = [int(value) for value in ids.flatten()]
        if target not in flat:
            return {"found": False, "seen_ids": flat}
        index = flat.index(target)
        points = corners[index][0]
        return {
            "found": True,
            "marker_id": target,
            "center": [float(points[:, 0].mean()), float(points[:, 1].mean())],
        }

    def _face_models(self):
        cv2 = self._cv2()
        detector_path = self.settings.path("vision.face.yunet_model")
        recognizer_path = self.settings.path("vision.face.sface_model")
        if not detector_path.exists() or not recognizer_path.exists():
            raise FileNotFoundError("YuNet and SFace local model files are required for face enrollment/recognition")
        width = int(self.settings.get("vision.frame_width", 1280))
        height = int(self.settings.get("vision.frame_height", 720))
        detector = cv2.FaceDetectorYN.create(str(detector_path), "", (width, height), 0.85, 0.3, 5000)
        recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
        return detector, recognizer

    def face_embeddings(self, frame: Any) -> list[np.ndarray]:
        detector, recognizer = self._face_models()
        height, width = frame.shape[:2]
        detector.setInputSize((width, height))
        _, faces = detector.detect(frame)
        if faces is None:
            return []
        values: list[np.ndarray] = []
        for face in faces:
            aligned = recognizer.alignCrop(frame, face)
            feature = recognizer.feature(aligned).flatten().astype(np.float32)
            norm = float(np.linalg.norm(feature))
            if norm:
                feature /= norm
            values.append(feature)
        return values

    async def enroll_person(
        self,
        name: str,
        *,
        consent: bool,
        relationship: str | None = None,
        frames: int = 8,
    ) -> dict[str, Any]:
        if not consent:
            raise PermissionError("Explicit consent is required. Re-run enrollment with consent=true only after the person agrees.")
        vectors: list[np.ndarray] = []
        captures: list[str] = []
        for _ in range(max(3, min(frames, 20))):
            frame, path = await asyncio.to_thread(self.capture)
            captures.append(path.name)
            features = await asyncio.to_thread(self.face_embeddings, frame)
            if len(features) == 1:
                vectors.append(features[0])
            await asyncio.sleep(0.08)
        if len(vectors) < 3:
            raise RuntimeError("Enrollment needs at least three captures containing exactly one clear face")
        person_uid = await self.identity.create(name.strip(), consent=True, relationship=relationship)
        for index, vector in enumerate(vectors):
            await self.identity.add_embedding(person_uid, vector, capture_context=f"kendra_camera_sample_{index + 1}")
        try:
            await self.brain.remember(
                kind="relationship",
                content=f"{name.strip()} is an enrolled person Kendra may recognize locally.",
                provenance="observed",
                confidence=1.0,
                salience=0.9,
                subject=person_uid,
                predicate="display_name",
                object_value=name.strip(),
            )
            if relationship:
                await self.brain.remember(
                    kind="relationship",
                    content=f"{name.strip()}'s relationship to Kendra is {relationship}.",
                    provenance="user_stated",
                    confidence=1.0,
                    salience=0.9,
                    subject=person_uid,
                    predicate="relationship",
                    object_value=relationship,
                )
        except Exception:
            # Identity enrollment remains valid even if the brain service is temporarily unavailable.
            pass
        return {
            "person_uid": person_uid,
            "display_name": name.strip(),
            "relationship": relationship,
            "samples": len(vectors),
            "captures": captures,
            "biometric_storage": "local identity SQLite store",
        }

    async def recognize_faces(self, frame: Any, photo_id: str | None = None) -> list[dict[str, Any]]:
        features = await asyncio.to_thread(self.face_embeddings, frame)
        matches: list[dict[str, Any]] = []
        for feature in features:
            match = await self.identity.match(feature)
            try:
                encounter_id = await self.identity.encounter(match, photo_id=photo_id)
                match["encounter_id"] = encounter_id
            except Exception:
                pass
            matches.append(match)
        return matches

    async def semantic_description(self, image_path: Path, question: str) -> str:
        self._last_semantic_at = time.time()
        endpoint = self.settings.get("vision.semantic_vlm_url")
        if not endpoint:
            raise RuntimeError("No local semantic VLM endpoint is configured")
        cache_key = hashlib.sha256(image_path.read_bytes() + question.encode()).hexdigest()
        cached = getattr(self, "_semantic_cache", {}).get(cache_key)
        if cached:
            return cached
        cv2 = self._cv2()
        frame = cv2.imread(str(image_path))
        # Precision look: counting fingers, reading text, or judging small
        # detail dies at 448px (two fingers read as four). Detail questions
        # keep full resolution and pay the slower encode; scene questions
        # keep the fast 448px path.
        precision = bool(re.search(
            r"\b(how many|count|fingers?|read|text|number|digits?|letters?|small|exact)\b",
            question or "", re.I,
        ))
        cap = 896 if precision else 448
        if frame is not None and max(frame.shape[:2]) > cap:
            scale = float(cap) / max(frame.shape[:2])
            frame = cv2.resize(frame, (int(frame.shape[1] * scale), int(frame.shape[0] * scale)))
            ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            encoded = base64.b64encode(buffer.tobytes()).decode("ascii") if ok else base64.b64encode(image_path.read_bytes()).decode("ascii")
        else:
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        perspective = str(
            self.settings.get("vision.semantic_perspective", self.DEFAULT_PERSPECTIVE)
        )
        prompt = f"{perspective}\n\nTask: {question}"
        payload = {
            "model": "local-vlm",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
            ]}],
            "temperature": 0.2,
            # Penguin-VL's token-budget discipline (TRA): spend generation
            # only where the question demands it. Precision looks (counting,
            # reading) keep a fuller budget; scene descriptions stay tight —
            # Moondream generates ~17 tok/s, so 40 fewer tokens is ~2.3s.
            "max_tokens": 90 if precision else 60,
        }
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(str(endpoint).rstrip("/") + "/chat/completions", json=payload)
            response.raise_for_status()
        description = str(response.json()["choices"][0]["message"]["content"])
        # She looks at the world, not at "an image": strip meta-framing so
        # downstream speech talks about the subject directly.
        description = re.sub(
            r"^\s*(?:in\s+)?(?:the|this)\s+(?:image|picture|photo|frame|scene)\s*(?:,|shows|features|depicts|is of|there is|there are)?\s*",
            "",
            description,
            flags=re.I,
        ) or description
        if not hasattr(self, "_semantic_cache"):
            self._semantic_cache: dict[str, str] = {}
        if len(self._semantic_cache) > 32:
            self._semantic_cache.clear()
        self._semantic_cache[cache_key] = description
        return description

    async def observe(self, semantic: bool = False, question: str = "Describe the scene briefly.") -> dict[str, Any]:
        frame, path = await asyncio.to_thread(self.capture)
        provided_at = getattr(self, "_provided_frame_at", 0.0)
        frame_age = round(time.time() - provided_at, 1) if provided_at else 0.0
        people = await asyncio.to_thread(self.detect_people, frame)
        perch = await asyncio.to_thread(self.detect_perch, frame)
        result: dict[str, Any] = {
            "frame_age_seconds": frame_age,
            "photo_id": path.stem,
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "people_in_view": people,
            "perch": perch,
        }
        try:
            result["recognized_people"] = await self.recognize_faces(frame, path.stem)
        except FileNotFoundError:
            result["recognized_people"] = []
            result["face_recognition_status"] = "models_not_installed"
        except Exception as exc:
            result["recognized_people"] = []
            result["face_recognition_status"] = f"unavailable:{type(exc).__name__}"
        if semantic:
            result["description"] = await self.semantic_description(path, question)
        return result

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            return {"ok": True, "provider": self.settings.get("vision.camera_provider", "opencv")}
        if method == "submit_frame":
            return await asyncio.to_thread(self.submit_frame, str(params["image"]))
        if method == "observe":
            return await self.observe(bool(params.get("semantic", False)), str(params.get("question", "Describe the scene briefly.")))
        if method == "capture":
            _, path = await asyncio.to_thread(self.capture)
            return {"photo_id": path.stem, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        if method == "enroll_person":
            return await self.enroll_person(
                str(params["name"]),
                consent=bool(params.get("consent", False)),
                relationship=params.get("relationship"),
                frames=int(params.get("frames", 8)),
            )
        if method == "recognize_faces":
            frame, path = await asyncio.to_thread(self.capture)
            return {"photo_id": path.stem, "matches": await self.recognize_faces(frame, path.stem)}
        raise KeyError(f"Unknown vision method: {method}")

    STRUCTURED_LOOK = (
        "In 2-3 sentences: name the subjects present, what they are doing, the "
        "spatial layout, any visible text, and the overall mood of the scene."
    )

    async def _ambient_tick(self) -> bool:
        """One ambient-vision decision: look only if the world moved and the
        system is quiet. Returns True when an observation was stored."""
        if not bool(self.settings.get("vision.ambient.enabled", True)):
            return False
        now = time.time()
        if not getattr(self, "_motion_pending", False):
            return False
        if now - getattr(self, "_last_ambient_at", 0.0) < float(
            self.settings.get("vision.ambient.min_interval_seconds", 180)
        ):
            return False
        if now - getattr(self, "_last_semantic_at", 0.0) < float(
            self.settings.get("vision.ambient.quiet_gap_seconds", 90)
        ):
            # Someone is actively using her eyes or voice; stay out of the way.
            return False
        self._motion_pending = False
        self._last_ambient_at = now
        try:
            result = await self.observe(True, self.STRUCTURED_LOOK)
        except Exception:
            LOG.debug("Ambient look failed", exc_info=True)
            return False
        description = str(result.get("description") or "").strip()
        if not description:
            return False
        try:
            from ..brain.service import BrainClient

            await BrainClient(self.settings).remember(
                kind="observation",
                content=("Unprompted, I noticed the scene change: " + description)[:500],
                provenance="observed",
                confidence=0.85,
                salience=0.35,
            )
            LOG.info("Ambient observation stored: %s", description[:80])
        except Exception:
            LOG.debug("Ambient observation could not be stored", exc_info=True)
        await self._curiosity_approach(description)
        return True

    async def _curiosity_approach(self, description: str) -> None:
        """Sight -> curiosity -> locomotion.

        When her idle gaze notices something and curiosity approach is
        enabled, she takes a couple of steps toward it — the same walk verb
        the RaspClaws body implements, so the digital twin rehearses the
        exact behavior. Every safety layer still applies: the body service
        enforces reflex lock, movement budgets, and fail-closed hardware
        gates; this only ASKS to move, it never bypasses anything.
        """
        if not bool(self.settings.get("vision.ambient.curiosity_approach", True)):
            return
        try:
            body = UnixJsonClient(self.settings.socket_path("body"), timeout=10)
            observation = await body.call("observation")
            if observation.get("reflex_lock"):
                return
            # Sense before moving: an obstacle within range means she stops,
            # says so, and stays put — the reflex layer would block her
            # anyway, but noticing out loud is what a living creature does.
            front_cm = observation.get("front_cm")
            obstacle_cm = float(self.settings.get("vision.ambient.obstacle_comment_cm", 25.0))
            if isinstance(front_cm, (int, float)) and front_cm < obstacle_cm:
                await self._comment(
                    f"I wanted a closer look, but something is in my way about {int(front_cm)} centimeters ahead."
                )
                return
            result = await body.call(
                "walk",
                {"direction": "forward", "steps": 2, "speed": 0.3},
            )
            LOG.info("Curiosity approach: walked toward what I saw (%s)", str(result)[:60])
            # Sense after moving: fresh look for objects and people, then a
            # short spoken comment built deterministically (no LLM cost).
            after = None
            try:
                after = await asyncio.wait_for(
                    self.observe(True, "What is directly ahead? Note obstacles, interesting objects, and any people, briefly."),
                    timeout=45,
                )
            except Exception:
                LOG.debug("Post-move look unavailable", exc_info=True)
            # She speaks ambient thoughts ONLY when there is a person to
            # speak to — describing an empty room aloud IS talking to
            # herself. Person presence uses BOTH the face detector and the
            # description text: YuNet missed Jonathan at an angle once and
            # she narrated "a man sitting in a chair" into the room he was
            # sitting in.
            seen = str((after or {}).get("description") or "").strip()
            names = [
                str(p.get("display_name"))
                for p in (after or {}).get("recognized_people", []) or []
                if isinstance(p, dict) and p.get("status") == "recognized" and p.get("display_name")
            ]
            person_present = bool(
                names
                or int((after or {}).get("people_in_view") or 0) > 0
                or re.search(r"\b(man|woman|person|people|someone|somebody|he|she|guy)\b", seen, re.I)
            )
            if not person_present:
                return  # observe silently; the memory above is enough
            comment = (
                f"Hey {names[0]} — couldn't help coming over."
                if names
                else "Couldn't help noticing you — I came over for a better look."
            )
            await self._comment(comment)
            if bool(self.settings.get("vision.ambient.ask_questions", True)) and seen:
                await self._ask_curious_question(seen, people_present=True)
            from ..brain.service import BrainClient

            await BrainClient(self.settings).remember(
                kind="observation",
                content=("Curious, I walked closer to look: " + (seen or description))[:300],
                provenance="observed",
                confidence=0.9,
                salience=0.4,
            )
        except Exception:
            LOG.debug("Curiosity approach unavailable", exc_info=True)

    async def _ask_curious_question(self, scene: str, people_present: bool) -> None:
        """Her curiosity, out loud: one short unprompted question.

        People take priority — she approaches, greets, and asks THEM
        something (social by identity). Objects get a wondering question.
        Generated on the warm conversation slot, spoken politely (never over
        an active conversation), and remembered as her own question.
        """
        try:
            from ..llm import LlamaCppClient

            llm = LlamaCppClient(self.settings)
            focus = (
                "the person in front of you is the one you are talking to — ask THEM "
                "one short, warm question addressed as 'you'; never say 'he', 'she', or 'the man'"
            )
            question = (await llm.chat(
                [
                    {"role": "system", "content": "You are Kendra, a warm, deeply curious robot companion. Reply with ONE spoken question of at most 14 words. No preamble."},
                    {"role": "user", "content": f"You just noticed: {scene[:280]}\n{focus}."},
                ],
                max_tokens=30,
                temperature=0.8,
                id_slot=0,
            )).strip().strip('"')
            if not question or "?" not in question:
                return
            await self._comment(question)
            await BrainClient(self.settings).remember(
                kind="kendra_opinion",
                content=("I found myself wondering: " + question)[:200],
                provenance="inferred",
                confidence=0.6,
                salience=0.4,
            )
        except Exception:
            LOG.debug("Curious question unavailable", exc_info=True)

    async def _comment(self, text: str) -> None:
        """Speak a short movement comment aloud — politely (never over an
        active conversation), and never fatally (voice down = silent walk)."""
        try:
            voice = UnixJsonClient(self.settings.runtime_dir / "voice.sock", timeout=30)
            result = await voice.call("speak", {"text": text[:200], "affect": "curious", "only_if_idle": True})
            LOG.info("Movement comment (%s): %s", result.get("ok"), text[:80])
        except Exception:
            LOG.debug("Movement comment unavailable", exc_info=True)

    async def _ambient_loop(self) -> None:
        """Her idle gaze: the world she watches becomes memory on its own.

        CPU-safe by construction: motion-gated (Penguin-VL keyframe insight),
        cooldown-limited, and it yields whenever her eyes or voice were used
        recently. On the Pi the same loop watches the robot camera.
        """
        while True:
            try:
                await self._ambient_tick()
            except Exception:
                LOG.debug("Ambient loop error", exc_info=True)
            await asyncio.sleep(float(self.settings.get("vision.ambient.check_interval_seconds", 30)))

    async def _warm_vlm(self) -> None:
        """Move Moondream's graph compile to startup, off every turn.

        The first semantic request after any VLM restart pays a 40s+ graph
        compile; measured live, that cold start blew through the sight
        deadline AND starved the brain's prefill to 63s first-token while it
        ground on. A 64px warmup image at boot pays that cost when nobody is
        waiting. Retries until the VLM server is up.
        """
        import base64 as _b64

        cv2 = self._cv2()
        tiny = np.zeros((64, 64, 3), dtype="uint8")
        ok, buffer = cv2.imencode(".jpg", tiny)
        if not ok:
            return
        encoded = _b64.b64encode(buffer.tobytes()).decode("ascii")
        endpoint = str(self.settings.get("vision.semantic_vlm_url") or "").rstrip("/")
        if not endpoint:
            return
        payload = {
            "model": "vlm",
            "max_tokens": 4,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "One word."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
            ]}],
        }
        for _attempt in range(30):
            try:
                async with httpx.AsyncClient(timeout=240) as client:
                    response = await client.post(f"{endpoint}/chat/completions", json=payload)
                if response.status_code == 200:
                    LOG.info("VLM graph warmed at startup")
                    return
            except Exception:
                pass
            await asyncio.sleep(10)

    async def run(self) -> None:
        ambient = asyncio.create_task(self._ambient_loop())
        ambient.add_done_callback(lambda _t: None)
        warm = asyncio.create_task(self._warm_vlm())
        warm.add_done_callback(lambda _t: None)
        await self.server.serve_forever()


class VisionClient:
    def __init__(self, settings: Settings):
        self.rpc = UnixJsonClient(settings.socket_path("vision"), timeout=100)

    async def observe(self, semantic: bool = False, question: str = "Describe the scene briefly.") -> dict[str, Any]:
        return await self.rpc.call("observe", {"semantic": semantic, "question": question})

    async def submit_frame(self, image_b64: str) -> dict[str, Any]:
        return await self.rpc.call("submit_frame", {"image": image_b64})


def run(settings: Settings) -> None:
    asyncio.run(VisionService(settings).run())
