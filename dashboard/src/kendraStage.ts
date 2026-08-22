/**
 * Kendra's on-screen body.
 *
 * Tripo auto-rigged her with a BIPED skeleton (Hip / Thigh / Calf) because
 * that is the only rig it offers, and driving an eight-legged spider with
 * it tore the mesh into spaghetti. Her generated likeness is good; the
 * retargeted skeleton is not. So this renders the UNRIGGED model — her real
 * geometry and textures — and animates it procedurally instead.
 *
 * Procedural motion also solves the other asset limitation: the mesh has no
 * morph targets, so there is nothing to blend for a blink or an expression.
 * Whole-body motion (breath, sway, lean, hop, recoil) is what sells life on
 * a creature this shape anyway — spiders emote with their whole body.
 *
 * Nothing here touches the robot. This is Virtual Kendra's puppet only.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

export type KendraMood =
  | "idle" | "listening" | "thinking" | "talking" | "singing"
  | "walking" | "running" | "curious" | "delighted" | "startled";

type MoodMotion = {
  /** vertical bob height in metres */ bob: number;
  /** bob speed multiplier */ bobRate: number;
  /** side-to-side sway in radians */ sway: number;
  /** sway speed */ swayRate: number;
  /** forward lean in radians (positive leans toward the viewer) */ lean: number;
  /** one-shot impulse height */ hop: number;
};

const MOTION: Record<KendraMood, MoodMotion> = {
  // Breath: barely there, but a perfectly still creature reads as dead.
  idle:      { bob: 0.012, bobRate: 1.1, sway: 0.020, swayRate: 0.45, lean: 0.00, hop: 0 },
  listening: { bob: 0.008, bobRate: 0.8, sway: 0.010, swayRate: 0.35, lean: 0.10, hop: 0 },
  thinking:  { bob: 0.010, bobRate: 0.7, sway: 0.060, swayRate: 0.30, lean: -0.05, hop: 0 },
  talking:   { bob: 0.020, bobRate: 2.2, sway: 0.035, swayRate: 1.10, lean: 0.05, hop: 0 },
  singing:   { bob: 0.045, bobRate: 2.6, sway: 0.180, swayRate: 1.60, lean: 0.02, hop: 0 },
  walking:   { bob: 0.035, bobRate: 5.0, sway: 0.070, swayRate: 2.50, lean: 0.08, hop: 0 },
  running:   { bob: 0.055, bobRate: 7.5, sway: 0.090, swayRate: 3.60, lean: 0.14, hop: 0 },
  curious:   { bob: 0.014, bobRate: 1.2, sway: 0.030, swayRate: 0.60, lean: 0.22, hop: 0 },
  delighted: { bob: 0.030, bobRate: 3.0, sway: 0.120, swayRate: 2.20, lean: 0.04, hop: 0.10 },
  startled:  { bob: 0.010, bobRate: 1.0, sway: 0.020, swayRate: 0.50, lean: -0.28, hop: 0.05 },
};

export class KendraStage {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private body = new THREE.Group();
  private lids: THREE.Mesh[] = [];
  private mood: KendraMood = "idle";
  private motion = MOTION.idle;
  private blend = MOTION.idle;
  private lastFrameAt = performance.now();
  private nextBlink = performance.now() + 2600;
  private blinkUntil = 0;
  private impulse = 0;
  private ready = false;

  constructor(private canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;

    this.camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
    this.scene.add(this.body);

    // Three-point-ish lighting: she is fuzzy and dark-eyed, and flat light
    // makes her read as a grey lump.
    const key = new THREE.DirectionalLight(0xfff4e6, 2.6);
    key.position.set(2.2, 3.2, 3.4);
    const fill = new THREE.DirectionalLight(0xcfe4ff, 1.1);
    fill.position.set(-3.0, 1.0, 1.5);
    const rim = new THREE.DirectionalLight(0xffffff, 1.4);
    rim.position.set(0, 2.0, -3.5);
    this.scene.add(key, fill, rim, new THREE.HemisphereLight(0xffffff, 0x8899aa, 0.85));
    this.resize();
  }

  /** Named animation clips found on the installed model, if it has any. */
  private mixer: THREE.AnimationMixer | null = null;
  private clips = new Map<string, THREE.AnimationAction>();
  private currentClip: THREE.AnimationAction | null = null;
  /** Morph-target influences for blinking, when the model provides them. */
  private blinkMorphs: Array<{ mesh: THREE.Mesh; index: number }> = [];

  async load(base = "./kendra3d"): Promise<void> {
    const loader = new GLTFLoader();
    // Prefer the purpose-rigged model Jonathan supplies; fall back to the
    // generated one. install_kendra_model.py writes kendra-body.glb.
    let gltf;
    try {
      gltf = await loader.loadAsync(`${base}/kendra-body.glb`);
    } catch {
      gltf = await loader.loadAsync(`${base}/kendra.glb`);
    }
    const model = gltf.scene;

    // Use real animation clips when the model ships them, since a rig built
    // for this body will always beat procedural motion.
    if (gltf.animations.length) {
      this.mixer = new THREE.AnimationMixer(model);
      for (const clip of gltf.animations) {
        const action = this.mixer.clipAction(clip);
        action.setLoop(THREE.LoopRepeat, Infinity);
        this.clips.set(clip.name.toLowerCase().replace(/^preset:/, ""), action);
      }
    }

    // Morph targets are the right way to blink: they animate the eyes
    // independently of whatever the body is doing.
    model.traverse((node) => {
      const mesh = node as THREE.Mesh;
      const dictionary = (mesh as unknown as { morphTargetDictionary?: Record<string, number> })
        .morphTargetDictionary;
      if (!dictionary) return;
      for (const [name, index] of Object.entries(dictionary)) {
        if (/blink|eye.*close|close.*eye|lid/i.test(name)) {
          this.blinkMorphs.push({ mesh, index });
        }
      }
    });

    // Tripo's scale and origin are arbitrary — normalise to a known height
    // and stand her on the floor, centred.
    const box = new THREE.Box3().setFromObject(model);
    const size = new THREE.Vector3();
    box.getSize(size);
    model.scale.setScalar(1.0 / Math.max(size.y, 0.001));
    box.setFromObject(model);
    const centre = new THREE.Vector3();
    box.getCenter(centre);
    model.position.set(-centre.x, -box.min.y, -centre.z);
    this.body.add(model);

    // Frame her whole body with a little air, slightly above eye level so
    // the viewer is looking gently down at a small creature.
    box.setFromObject(this.body);
    box.getSize(size);
    const reach = Math.max(size.x, size.y, size.z);
    this.camera.position.set(0, size.y * 0.72, reach * 2.15);
    this.camera.lookAt(0, size.y * 0.45, 0);

    if (!this.blinkMorphs.length) {
      // No morph targets: fall back to added geometry.
      this.buildLids(size);
    }
    this.ready = true;
    this.renderer.setAnimationLoop(() => this.tick());
  }

  /**
   * Eyelids. There are no morph targets, so a blink has to be geometry:
   * two lids sized and placed from her bounding box, sitting just proud of
   * her eyes and closing for ~110 ms.
   */
  private buildLids(size: THREE.Vector3): void {
    const radius = size.x * 0.085;
    const geometry = new THREE.SphereGeometry(radius, 20, 14);
    const material = new THREE.MeshStandardMaterial({
      color: 0x2a4f5c, roughness: 0.45, metalness: 0.05,
    });
    for (const side of [-1, 1]) {
      const lid = new THREE.Mesh(geometry, material);
      lid.position.set(size.x * 0.105 * side, size.y * 0.78, size.z * 0.30);
      lid.scale.y = 0.02;
      lid.visible = false; // enabled once calibrated against the real eyes
      this.body.add(lid);
      this.lids.push(lid);
    }
  }

  /** Clip names to try for each mood, best first. */
  private static CLIP_FOR: Record<KendraMood, string[]> = {
    idle: ["idle", "breathe"],
    listening: ["lean_forward", "listen", "idle"],
    thinking: ["think", "idle"],
    talking: ["talk", "idle"],
    singing: ["sway", "dance", "sing", "idle"],
    walking: ["walk"],
    running: ["run", "walk"],
    curious: ["lean_forward", "climb", "idle"],
    delighted: ["excited", "bounce", "jump", "idle"],
    startled: ["recoil", "hurt", "idle"],
  };

  setMood(mood: KendraMood): void {
    if (mood === this.mood) return;
    this.mood = mood;
    this.motion = MOTION[mood] ?? MOTION.idle;
    if (this.motion.hop > 0) this.impulse = this.motion.hop;

    if (!this.clips.size) return;  // procedural only
    for (const name of KendraStage.CLIP_FOR[mood] ?? ["idle"]) {
      const next = this.clips.get(name);
      if (!next) continue;
      next.enabled = true;
      next.setEffectiveWeight(1);
      next.play();
      // Crossfade: switching clips instantly reads as a glitch.
      if (this.currentClip && this.currentClip !== next) {
        this.currentClip.crossFadeTo(next, 0.35, false);
      }
      this.currentClip = next;
      return;
    }
  }

  private tick(): void {
    if (!this.ready) return;
    const now = performance.now();
    const delta = Math.min((now - this.lastFrameAt) / 1000, 0.1);
    this.lastFrameAt = now;
    const seconds = now / 1000;
    this.mixer?.update(delta);

    // Ease between moods so a state change is a movement, not a jump cut.
    const k = 1 - Math.exp(-delta * 3.5);
    this.blend = {
      bob: this.blend.bob + (this.motion.bob - this.blend.bob) * k,
      bobRate: this.blend.bobRate + (this.motion.bobRate - this.blend.bobRate) * k,
      sway: this.blend.sway + (this.motion.sway - this.blend.sway) * k,
      swayRate: this.blend.swayRate + (this.motion.swayRate - this.blend.swayRate) * k,
      lean: this.blend.lean + (this.motion.lean - this.blend.lean) * k,
      hop: 0,
    };

    this.impulse = Math.max(0, this.impulse - delta * 0.35);
    const hop = Math.abs(Math.sin(seconds * 7.0)) * this.impulse;

    const damp = this.clips.size ? 0.25 : 1.0;
    this.body.position.y =
      Math.sin(seconds * this.blend.bobRate * Math.PI) * this.blend.bob * damp + hop * damp;
    this.body.rotation.z =
      Math.sin(seconds * this.blend.swayRate * Math.PI) * this.blend.sway * damp;
    this.body.rotation.x = this.blend.lean;
    // A slow turn keeps her from looking like a photograph.
    this.body.rotation.y = Math.sin(seconds * 0.18) * 0.10;

    if (now >= this.nextBlink) {
      this.blinkUntil = now + 110;
      this.nextBlink = now + 2400 + Math.random() * 4000;
    }
    const closed = now < this.blinkUntil;
    if (this.blinkMorphs.length) {
      for (const { mesh, index } of this.blinkMorphs) {
        const influences = mesh.morphTargetInfluences;
        if (!influences) continue;
        influences[index] += ((closed ? 1 : 0) - influences[index]) * 0.5;
      }
    } else {
      for (const lid of this.lids) {
        lid.scale.y += ((closed ? 1.0 : 0.02) - lid.scale.y) * 0.5;
      }
    }

    this.renderer.render(this.scene, this.camera);
  }

  resize(): void {
    const width = this.canvas.clientWidth || 320;
    const height = this.canvas.clientHeight || 320;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  dispose(): void {
    this.renderer.setAnimationLoop(null);
    this.renderer.dispose();
  }
}
