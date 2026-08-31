/**
 * Kendra, drawn instead of modelled.
 *
 * The 3D route cost 816 KB of three.js and a 9.3 MB GLB fetched before she
 * could appear, and every asset fought us: Tripo's rigger forced a biped
 * skeleton onto a spider, a supplied octopus had a clean rig but was not
 * her, and a hexapod download turned out to be STL printing parts. None of
 * that difficulty was ever about animation — it was about assets.
 *
 * So there is no asset. She is ~40 points of physics on a 2D canvas.
 *
 * WHY THIS LOOKS ALIVE. Her tentacles are not animated; they are DRAGGED.
 * Each is a Verlet chain whose first link is pinned to her body, so when the
 * body moves the tentacles follow a frame late, overshoot, and settle. That
 * lag is "secondary motion", the thing animators add by hand to make a
 * character feel like it has mass — and here it falls out of the integrator
 * for free. Nothing in this file describes a wave or a curl; every wave and
 * curl you see is the consequence of her body moving and physics catching up.
 *
 * WHY IT IS RESPONSIVE. A mood does not start a timeline that has to finish.
 * It moves a handful of TARGETS — where she sits, how hard her tentacles are
 * pushed outward, how open her eyes are — and the springs chase them. A mood
 * change is visible on the very next frame and fully settled in about 200 ms,
 * with no clip to interrupt and no crossfade to schedule.
 *
 * Inspired by the staggered-primitive trick in tmip: three identical dots on
 * one keyframe with 0.15 s of delay between them read as a single living
 * thing. Every tentacle here carries a phase offset for the same reason.
 */

export type KendraMood =
  | "idle" | "listening" | "thinking" | "talking" | "singing"
  | "walking" | "running" | "curious" | "delighted" | "startled" | "greeting"
  | "researching" | "looking";

type Feeling = {
  /** how high she rides, as a fraction of her body radius */ lift: number;
  /** vertical bob height */ bob: number;
  /** bob speed, cycles per second */ bobRate: number;
  /** side-to-side travel */ sway: number;
  /** sway speed */ swayRate: number;
  /** downward pull on the tentacles; negative floats them up */ droop: number;
  /** how far the tentacles are pushed away from her body */ reach: number;
  /** how much a tentacle's own phase wanders it about */ wander: number;
  /** wander speed */ wanderRate: number;
  /** 1 is wide open, 0 is shut */ eyes: number;
  /** pupil size, fraction of the eye */ pupil: number;
  /** mouth curve: +1 a smile, -1 a frown */ smile: number;
  /** how far the mouth opens */ mouthOpen: number;
  /** squash: >1 is tall and thin, <1 is wide and flat */ stretch: number;
  /** seconds between blinks */ blinkEvery: number;
  /** her colour for this feeling, in degrees of hue */ hue: number;
  /** how saturated that colour runs, 0-1 */ sat: number;
};

/**
 * HER COLOUR IS A FEELING, NOT A CONSTANT.
 *
 * Jonathan's idea, and a better one than any of mine: let the conversation's
 * sentiment move her colour, and make that the SAME signal that later drives
 * the LED ring on the Pi. One emotional channel, rendered as pixels here and
 * as light on the robot — so the behaviour transfers instead of being
 * rebuilt.
 *
 * That is why `paletteFor()` below returns a hue/saturation pair rather than
 * touching the canvas: it is the wire format. `KendraSprite.emotion()`
 * exposes it for whatever wants to consume it, and `kendra/leds/` is the
 * intended second consumer.
 *
 * Hues are chosen so the reading is immediate: teal at rest, warming through
 * gold as she is pleased, cooling to indigo when she is thinking, and pushing
 * to alarm-coral when startled. Saturation carries intensity separately from
 * hue, so "quietly content" and "delighted" are the same colour at different
 * volumes.
 */

/**
 * Every feeling she has, as eleven numbers.
 *
 * These are the whole performance. There is no other animation data in the
 * project — no clips, no keyframes, no rig. Tuning her personality means
 * editing this table, which is the entire point of the approach.
 */
const FEELING: Record<KendraMood, Feeling> = {
  // Breathing, tentacles hanging soft. Never perfectly still: a static
  // creature reads as switched off within about two seconds.
  idle:      { lift: 0.00, bob: 0.05, bobRate: 0.45, sway: 0.03, swayRate: 0.22, droop: 0.55, reach: 0.30, wander: 0.16, wanderRate: 0.5, eyes: 1.00, pupil: 0.42, smile: 0.35, mouthOpen: 0.05, stretch: 1.00, blinkEvery: 4.0, hue: 183, sat: 0.34 },
  // Leaning in, eyes wide, body still — stillness IS attention.
  listening: { lift: 0.06, bob: 0.02, bobRate: 0.35, sway: 0.01, swayRate: 0.18, droop: 0.45, reach: 0.34, wander: 0.07, wanderRate: 0.4, eyes: 1.18, pupil: 0.50, smile: 0.30, mouthOpen: 0.04, stretch: 0.98, blinkEvery: 5.5, hue: 192, sat: 0.4 },
  // Looking up and away, one slow drift. Thinking is not stillness, it is
  // distraction.
  thinking:  { lift: 0.02, bob: 0.03, bobRate: 0.30, sway: 0.08, swayRate: 0.28, droop: 0.40, reach: 0.28, wander: 0.26, wanderRate: 0.3, eyes: 0.80, pupil: 0.36, smile: 0.15, mouthOpen: 0.03, stretch: 1.02, blinkEvery: 3.0, hue: 232, sat: 0.36 },
  // Talking with her hands. The mouth is driven separately, per syllable.
  talking:   { lift: 0.03, bob: 0.07, bobRate: 1.35, sway: 0.05, swayRate: 0.85, droop: 0.25, reach: 0.44, wander: 0.34, wanderRate: 1.5, eyes: 1.02, pupil: 0.44, smile: 0.40, mouthOpen: 0.45, stretch: 1.00, blinkEvery: 4.5, hue: 178, sat: 0.46 },
  // Big slow sway, tentacles lifted and rolling.
  singing:   { lift: 0.10, bob: 0.14, bobRate: 1.05, sway: 0.20, swayRate: 0.70, droop: -0.10, reach: 0.58, wander: 0.50, wanderRate: 1.1, eyes: 0.72, pupil: 0.40, smile: 0.85, mouthOpen: 0.80, stretch: 1.04, blinkEvery: 6.0, hue: 275, sat: 0.58 },
  walking:   { lift: 0.02, bob: 0.11, bobRate: 2.30, sway: 0.09, swayRate: 1.15, droop: 0.50, reach: 0.40, wander: 0.30, wanderRate: 2.2, eyes: 1.00, pupil: 0.42, smile: 0.40, mouthOpen: 0.06, stretch: 1.00, blinkEvery: 4.0, hue: 183, sat: 0.4 },
  running:   { lift: 0.05, bob: 0.17, bobRate: 3.60, sway: 0.13, swayRate: 1.80, droop: 0.35, reach: 0.50, wander: 0.38, wanderRate: 3.2, eyes: 1.10, pupil: 0.38, smile: 0.45, mouthOpen: 0.30, stretch: 1.05, blinkEvery: 5.0, hue: 168, sat: 0.5 },
  // Head tilted, leaning at whatever caught her attention.
  curious:   { lift: 0.07, bob: 0.04, bobRate: 0.60, sway: 0.06, swayRate: 0.40, droop: 0.35, reach: 0.36, wander: 0.22, wanderRate: 0.8, eyes: 1.22, pupil: 0.54, smile: 0.45, mouthOpen: 0.14, stretch: 0.97, blinkEvery: 3.2, hue: 205, sat: 0.52 },
  // Bouncing. Eyes squeeze up into happy arcs, which is most of the read.
  delighted: { lift: 0.14, bob: 0.20, bobRate: 2.60, sway: 0.11, swayRate: 1.60, droop: -0.30, reach: 0.66, wander: 0.44, wanderRate: 2.4, eyes: 0.45, pupil: 0.52, smile: 1.00, mouthOpen: 0.55, stretch: 1.03, blinkEvery: 7.0, hue: 44, sat: 0.7 },
  // Everything PULLS IN. Wide eyes, tiny pupils, squashed low, tentacles
  // tucked. The one loud feeling that gets smaller instead of bigger.
  startled:  { lift: -0.10, bob: 0.02, bobRate: 0.80, sway: 0.01, swayRate: 0.30, droop: 0.85, reach: 0.12, wander: 0.05, wanderRate: 0.4, eyes: 1.45, pupil: 0.26, smile: -0.35, mouthOpen: 0.35, stretch: 0.88, blinkEvery: 9.0, hue: 8, sat: 0.62 },
  // One tentacle waves; see WAVING_TENTACLE below.
  greeting:  { lift: 0.11, bob: 0.10, bobRate: 1.50, sway: 0.09, swayRate: 1.00, droop: 0.05, reach: 0.52, wander: 0.30, wanderRate: 1.3, eyes: 1.10, pupil: 0.48, smile: 0.90, mouthOpen: 0.35, stretch: 1.01, blinkEvery: 5.0, hue: 58, sat: 0.6 },
  // Searching the world. A cool, deliberate green-teal that belongs to nothing
  // else she does, so "she is online, looking something up" is readable at a
  // glance across the room. Eyes wide and pupils small: scanning, not gazing.
  // One tentacle lifts to hold the glass — see MAGNIFIER_TENTACLE.
  researching: { lift: 0.05, bob: 0.03, bobRate: 0.55, sway: 0.04, swayRate: 0.35, droop: 0.34, reach: 0.38, wander: 0.14, wanderRate: 0.7, eyes: 1.26, pupil: 0.34, smile: 0.28, mouthOpen: 0.05, stretch: 0.99, blinkEvery: 3.6, hue: 150, sat: 0.50 },
  // USING HER EYES. She cranes up and forward, goes almost perfectly still,
  // and her pupils stop being pupils: they become a camera iris that closes
  // to a point and opens again as focus lands. Amber, like a focus-assist
  // lamp — nothing else she does is warm AND still, so it cannot be mistaken
  // for delight (which bounces with its eyes squeezed shut). She barely
  // blinks: you do not blink while you are trying to see something.
  looking:   { lift: 0.13, bob: 0.015, bobRate: 0.30, sway: 0.02, swayRate: 0.20, droop: 0.42, reach: 0.33, wander: 0.06, wanderRate: 0.35, eyes: 1.38, pupil: 0.46, smile: 0.20, mouthOpen: 0.04, stretch: 1.03, blinkEvery: 9.0, hue: 32, sat: 0.56 },
};


/**
 * Turn what she is about to SAY into a mouth.
 *
 * The first version was `sin(t*11)*0.6 + sin(t*6.3+1.1)*0.4` — invented
 * chatter that ran at the same rate whether she said "mm" or recited a
 * paragraph. It looked like a puppet because it was one: nothing connected
 * her mouth to her words.
 *
 * This builds a real envelope from the text. English syllables are roughly
 * vowel groups, so each one becomes a beat; punctuation becomes a rest,
 * because the silences are what make speech read as speech. Long vowels open
 * wider than short ones, and a question mark lifts the final beat.
 *
 * It is an approximation of her actual audio — the honest fix is an envelope
 * published by the voice service — but it is derived from the real utterance,
 * so the rhythm, the pauses and the length are all hers.
 */
type Beat = { start: number; span: number; peak: number };

export function speechBeats(text: string): { beats: Beat[]; total: number } {
  const beats: Beat[] = [];
  let cursor = 0;
  const words = (text || "").trim().split(/\s+/).filter(Boolean);
  for (const word of words) {
    const letters = word.replace(/[^a-z']/gi, "");
    // Vowel groups, minus a silent trailing "e" — close enough to syllables
    // for rhythm, and it costs one regex instead of a dictionary.
    let count = (letters.toLowerCase().match(/[aeiouy]+/g) || []).length;
    if (/[^aeiou]e$/i.test(letters) && count > 1) count -= 1;
    count = Math.max(1, count);
    for (let i = 0; i < count; i += 1) {
      // Long vowels and the stressed first syllable of a word open wider.
      const wide = /[aeiou]{2}|[aeiou](?:r|w)/i.test(letters) && i === 0;
      beats.push({ start: cursor, span: 1, peak: wide ? 1 : i === 0 ? 0.85 : 0.6 });
      cursor += 1;
    }
    const punctuation = /[.,;:!?)\]]+$/.exec(word)?.[0] ?? "";
    if (/[.!?]/.test(punctuation)) cursor += 2.2;        // sentence rest
    else if (/[,;:]/.test(punctuation)) cursor += 1.1;   // clause rest
    else cursor += 0.35;                                  // word gap
    if (/\?$/.test(word) && beats.length) beats[beats.length - 1].peak = 1;
  }
  return { beats, total: Math.max(cursor, 1) };
}

/**
 * What she is busy doing.
 *
 * These three names are NOT new. They are the vocabulary her thinking tones
 * and her LED ring have always used (`kendra/leds/service.py`: think =
 * breathing cyan, research = blue chase, sight = green ticks). The renderer
 * originally invented "search" and "look" instead, so every mode she emitted
 * matched nothing and fell through to plain thinking -- which is why the
 * research and sight animations never once appeared. One signal, one set of
 * names, everywhere.
 */
export type ThinkingMode = "think" | "research" | "sight";

/**
 * One thing she says, with the time it really takes.
 *
 * `durationMs` comes from the synthesized audio's sample count when the voice
 * service knows it (Kokoro), and is 0 when it does not (Piper streams, so its
 * length is unknown until it ends). An unknown duration is NOT a licence to
 * guess an end time: the beats are paced from a text estimate and the
 * utterance is held open until `endSpeech()` arrives, so her mouth stops when
 * the sound does rather than when a character count said it should.
 */
type Utterance = {
  beats: Beat[];
  total: number;
  startedAt: number;
  durationMs: number;
  openEnded: boolean;
};

/** Fallback pacing only — never an end time. ~13 characters a second. */
function estimateMs(text: string): number {
  return Math.min(14_000, Math.max(700, (text.trim().length / 13) * 1000));
}

/** What kind of sound she is making. A hum has no syllables. */
export type SpeechKind = "speech" | "song" | "hum" | "tune";

/**
 * A mouth for wordless singing.
 *
 * Humming and her synthesizer tunes carry no text, so `speechBeats` returns
 * nothing and her mouth stayed shut through the entire performance. A slow,
 * even open-and-close at roughly two a second reads as sustained voice rather
 * than as speech, which is what a hum actually looks like.
 */
export function sustainedBeats(seconds: number): { beats: Beat[]; total: number } {
  const span = 1;
  const count = Math.max(1, Math.round(Math.max(0.5, seconds) * 2));
  const beats: Beat[] = [];
  for (let i = 0; i < count; i += 1) {
    // Gentle variation so it breathes instead of ticking like a metronome.
    beats.push({ start: i * 1.35, span, peak: 0.62 + 0.18 * Math.sin(i * 1.1) });
  }
  return { beats, total: count * 1.35 };
}

const TENTACLES = 6;          // she is a hexapod
const LINKS = 7;              // points per tentacle; more = floppier
const WAVING_TENTACLE = 1;    // the one that waves hello, front-right
const MAGNIFIER_TENTACLE = 4; // the one that holds up the magnifying glass

type Point = { x: number; y: number; px: number; py: number };
type Tentacle = { points: Point[]; angle: number; phase: number };

export class KendraSprite {
  private ctx: CanvasRenderingContext2D;
  private tentacles: Tentacle[] = [];
  private mood: KendraMood = "idle";
  private want = FEELING.idle;
  private now: Feeling = { ...FEELING.idle };
  private lastFrame = performance.now();
  private blinkAt = 0;
  private blink = 0;
  private radius = 40;
  private cx = 0;
  private cy = 0;
  /** where her body actually is this frame — the tentacles chase it */
  private bodyX = 0;
  private bodyY = 0;
  private gaze = { x: 0, y: 0, tx: 0, ty: 0, next: 0 };
  /**
   * Where the CONVERSATION has drifted, independent of this turn's mood.
   * -1 is bleak, +1 is warm. A single sharp remark should not repaint her;
   * a warm ten minutes should. So this moves slowly and biases the mood hue
   * rather than replacing it.
   */
  /**
   * The utterance she is speaking, and the ones waiting behind it.
   *
   * This used to be a single slot that `speak()` overwrote. Combined with a
   * three-second poll that delivered whole finished replies, two replies
   * inside one window meant the first was cut dead and the second replayed
   * from zero — the animation "catching up and then rushing out all at once".
   * Phrases now queue and play in order, at their real durations.
   */
  private speech: Utterance | null = null;
  private queue: Utterance[] = [];
  /** Bubble above her head while she works. See `think()`. */
  private thinking: { mode: ThinkingMode; since: number; until: number } | null = null;
  /** How far the bubble has risen, 0..1. Eased so it grows and sinks. */
  private bubble = 0;
  /** The last frame's elapsed seconds, for time-based easing in helpers. */
  private lastDt = 1 / 60;
  /** How far the magnifying glass is raised, 0..1. See `researching`. */
  private hold = 0;
  /** How far the waving limb is raised, 0..1. See `greeting`. */
  private wave = 0;
  /** She cannot hear anything at all — drawn as a struck-through ear. */
  private deaf = false;
  /** How far the camera iris is engaged, 0..1, and when it started. */
  private iris = 0;
  private irisAt = 0;
  /**
   * The walk she is actually taking, straight from her body service.
   *
   * `cycleMs` is the vendor's real gait period (0.4 s: a 4-phase tripod at
   * 0.1 s a phase), so the legs on screen step at the rate the servos will.
   * Nothing here is decorative timing.
   */
  private gait: {
    action: "walk" | "turn";
    direction: "forward" | "backward" | "left" | "right";
    startedAt: number;
    durationMs: number;
    cycleMs: number;
    speed: number;
  } | null = null;
  /** Where the walk has carried her, in body radii, and her facing. */
  private travel = 0;
  private travelTarget = 0;
  private facing = 0;
  private facingTarget = 0;
  /** Approach/retreat, as a size change. 1 is where she normally stands. */
  private walkScale = 1;
  /**
   * A REACTION, distinct from a mood.
   *
   * Easing between two postures at a constant rate is why she read as
   * poorly timed: real feeling arrives faster than it leaves. Surprise is a
   * jolt then a recovery; delight is a bounce that settles. So a mood change
   * fires an impulse that decays over ~700 ms on top of the steady state,
   * and the size of the impulse is how far the feeling moved — a small shift
   * barely registers, a big one lands.
   */
  private jolt = 0;
  private joltAt = 0;
  private sentiment = 0;
  private sentimentTarget = 0;
  /** the colour she is actually wearing this frame */
  private hue = FEELING.idle.hue;
  private sat = FEELING.idle.sat;
  private raf = 0;
  private running = false;

  constructor(private canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Kendra needs a 2D canvas");
    this.ctx = ctx;
    this.resize();
    this.build();
  }

  /** Lay the tentacles out evenly, each starting straight down. */
  private build(): void {
    this.tentacles = [];
    for (let i = 0; i < TENTACLES; i += 1) {
      // Spread across the underside rather than the full circle: a creature
      // with limbs out of its scalp reads as a spider, not a companion.
      const spread = Math.PI * 1.28;
      const angle = -Math.PI / 2 + spread * (i / (TENTACLES - 1) - 0.5) + Math.PI;
      const points: Point[] = [];
      const step = (this.radius * 1.5) / LINKS;
      for (let k = 0; k < LINKS; k += 1) {
        const x = this.cx + Math.cos(angle) * step * k;
        const y = this.cy + Math.sin(angle) * step * k;
        points.push({ x, y, px: x, py: y });
      }
      // A phase offset per tentacle: the tmip dots trick. Identical motion
      // with staggered timing reads as one organism, not six puppets.
      this.tentacles.push({ points, angle, phase: (i / TENTACLES) * Math.PI * 2 });
    }
  }

  /**
   * She has STARTED saying this out loud, right now.
   *
   * Called from a `speech_start` event published as the first audio sample
   * reaches the speaker, so the mouth opens in the same frame the sound
   * begins. `seconds` is the utterance's true length; pass 0 when the voice
   * service cannot know it yet.
   *
   * If she is already speaking, this queues behind the current phrase rather
   * than replacing it — a streamed reply arrives as several phrases and they
   * must play in order, not on top of each other.
   */
  speak(text: string, seconds: number, kind: SpeechKind = "speech"): void {
    // A hum or a tune is her voice with no words in it. Driving the mouth
    // from syllables would leave it shut for the whole performance, which is
    // exactly what happened: every song she has sung, she sang with a closed
    // mouth. Wordless PERFORMANCE audio gets a sustained open-and-close.
    //
    // Ordinary speech does not: an empty string arriving on the speech path
    // means nothing was said, and inventing a mouth for it would animate her
    // through silence.
    const wordless = !text.trim();
    if (wordless && (kind === "speech" || seconds <= 0)) return;
    const built = wordless ? sustainedBeats(seconds) : speechBeats(text);
    if (!built.beats.length) return;
    const utterance: Utterance = {
      beats: built.beats,
      total: built.total,
      startedAt: 0,
      durationMs: seconds > 0 ? seconds * 1000 : estimateMs(text),
      openEnded: !(seconds > 0),
    };
    if (this.speech) this.queue.push(utterance);
    else this.begin(utterance);
  }

  private begin(utterance: Utterance): void {
    utterance.startedAt = performance.now();
    this.speech = utterance;
  }

  /**
   * The audio finished. Advances to the next queued phrase, if any.
   *
   * This is what makes an unknown duration safe: the picture ends because the
   * sound ended, not because an estimate expired.
   */
  endSpeech(): void {
    const next = this.queue.shift();
    if (next) this.begin(next);
    else this.speech = null;
  }

  /**
   * Where the thought bubble is on the canvas, or null when it is down.
   *
   * Exposed so "is it inside the frame" is a test rather than something you
   * only find out by looking at a screenshot with the top sliced off.
   */
  bubbleBox(): { left: number; right: number; top: number; bottom: number } | null {
    if (this.bubble < 0.02) return null;
    const r = this.radius * this.walkScale;
    const br = r * 0.62 * this.bubble;
    const w = this.canvas.clientWidth || 300;
    const margin = br + Math.max(2, r * 0.06);
    const bx = Math.max(margin, Math.min(w - margin, this.bodyX + r * 1.05));
    const by = Math.max(margin, this.bodyY - r * (1.5 + 0.28 * this.bubble));
    return { left: bx - br, right: bx + br, top: by - br, bottom: by + br };
  }

  /** Stop mid-sentence — she was interrupted. Drops everything waiting. */
  hush(): void {
    this.speech = null;
    this.queue.length = 0;
  }

  /** True while she has audio playing or waiting. */
  get speaking(): boolean {
    return this.speech !== null || this.queue.length > 0;
  }

  /**
   * She is working. Raises a thought bubble and lifts her eyes to it.
   *
   * Driven by the same event that starts her thinking tones, so the bubble,
   * the sound and (on the robot) the light are one signal and cannot drift
   * apart. `until` is a safety net only: if a service dies mid-thought the
   * bubble fades instead of hanging above her forever.
   */
  think(mode: ThinkingMode = "think"): void {
    const now = performance.now();
    this.thinking = { mode, since: this.thinking?.since ?? now, until: now + 60_000 };
  }

  /** Done working. */
  stopThinking(): void {
    this.thinking = null;
  }

  /**
   * Every microphone returned silence, so she genuinely cannot hear.
   *
   * Worth drawing rather than logging: an unauthorized microphone on macOS
   * opens fine and returns zeros forever, so a deaf Kendra is pixel-identical
   * to a waiting one. That ambiguity is the whole reason "she is dead" keeps
   * being the report.
   */
  setDeaf(value: boolean): void {
    this.deaf = value;
  }

  /**
   * She has started to move. Driven by her body service, in her real timing.
   *
   * The same call that publishes this event will drive the RaspClaws servos:
   * `cycleSeconds` is the vendor's 0.4 s tripod period and `cycles` is the
   * number of gait cycles the body service actually committed to, so the
   * animation is a readout of the motion rather than an impression of one.
   */
  move(
    action: "walk" | "turn",
    direction: "forward" | "backward" | "left" | "right",
    cycles: number,
    cycleSeconds: number,
    speed = 0.35,
  ): void {
    const cycleMs = Math.max(80, cycleSeconds * 1000);
    this.gait = {
      action,
      direction,
      startedAt: performance.now(),
      durationMs: Math.max(cycleMs, cycles * cycleMs),
      cycleMs,
      speed,
    };
    if (action === "turn") {
      // A turn is shown as her body swinging to face the new way.
      this.facingTarget += direction === "left" ? -0.55 : 0.55;
      this.facingTarget = Math.max(-1, Math.min(1, this.facingTarget));
    } else if (direction === "left" || direction === "right") {
      this.travelTarget += direction === "left" ? -0.5 : 0.5;
      this.travelTarget = Math.max(-1.4, Math.min(1.4, this.travelTarget));
    }
  }

  /** She stopped moving — commanded, or because the reflex layer said so. */
  stopMoving(): void {
    this.gait = null;
    // She does not snap back to centre: where a walk left her is where she is.
    this.travelTarget = this.travel;
  }

  /** True while her legs are actually stepping. */
  get moving(): boolean {
    if (!this.gait) return false;
    if (performance.now() - this.gait.startedAt > this.gait.durationMs) {
      this.gait = null;
      return false;
    }
    return true;
  }

  /**
   * How far through the current gait cycle each leg is, 0..1 per tripod.
   *
   * A hexapod walks on alternating TRIPODS: legs 0, 2, 4 lift together while
   * 1, 3, 5 carry, then they swap. It is the gait her vendor bridge drives
   * and the reason a six-legged walk reads as purposeful rather than as a
   * crawl — so the picture uses it too.
   */
  private tripodLift(index: number): number {
    const gait = this.gait;
    if (!gait) return 0;
    const elapsed = performance.now() - gait.startedAt;
    if (elapsed > gait.durationMs) return 0;
    const phase = (elapsed / gait.cycleMs + (index % 2 === 0 ? 0 : 0.5)) % 1;
    // Swing for the first half of the cycle, planted for the second.
    if (phase >= 0.5) return 0;
    return Math.sin(phase * Math.PI * 2) ;
  }

  /**
   * Nudge the conversation's emotional weather. Positive is warm.
   *
   * Deliberately additive and clamped rather than absolute: individual turns
   * are noisy, and a companion whose colour flips on one sentence reads as
   * unstable rather than responsive.
   */
  feel(valence: number): void {
    this.sentimentTarget = Math.max(-1, Math.min(1, this.sentimentTarget + valence * 0.35));
  }

  /**
   * Her current emotional colour — the signal, not the pixels.
   *
   * This is what `kendra/leds/` should consume on the Pi so the ring shows
   * the same feeling the screen does. Returned as hue/saturation plus an
   * rgb convenience triple, because an LED driver wants the former and a
   * canvas wants the latter.
   */
  emotion(): { mood: KendraMood; hue: number; sat: number; sentiment: number; rgb: [number, number, number] } {
    return {
      mood: this.mood,
      hue: Math.round(this.hue),
      sat: Number(this.sat.toFixed(3)),
      sentiment: Number(this.sentiment.toFixed(3)),
      rgb: hsl(this.hue, this.sat, 0.62),
    };
  }

  setMood(mood: KendraMood): void {
    if (mood === this.mood) return;
    const from = FEELING[this.mood] ?? FEELING.idle;
    const to = FEELING[mood] ?? FEELING.idle;
    this.mood = mood;
    this.want = to;
    // Distance travelled across the axes the eye actually reads.
    const distance =
      Math.abs(to.reach - from.reach) * 1.6 +
      Math.abs(to.lift - from.lift) * 2.4 +
      Math.abs(to.eyes - from.eyes) * 1.1 +
      Math.abs(to.stretch - from.stretch) * 2.0;
    // A jolt that is still decaying must not be topped back up to full: a
    // UI that changes her mood twice in a second would otherwise hold her at
    // maximum reaction indefinitely, which is the jitter.
    const now = performance.now();
    const remaining = this.jolt * Math.max(0, 1 - (now - this.joltAt) / 700);
    this.jolt = Math.min(1, Math.max(remaining, Math.min(1, distance) * (1 - remaining * 0.6)));
    this.joltAt = now;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.lastFrame = performance.now();
    const tick = () => {
      if (!this.running) return;
      this.frame();
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  stop(): void {
    this.running = false;
    cancelAnimationFrame(this.raf);
  }

  resize(): void {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = this.canvas.clientWidth || 300;
    const height = this.canvas.clientHeight || 300;
    this.canvas.width = Math.round(width * ratio);
    this.canvas.height = Math.round(height * ratio);
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.radius = Math.min(width, height) * 0.19;
    this.cx = width / 2;
    this.cy = height * 0.42;
  }

  private frame(): void {
    const now = performance.now();
    const dt = Math.min((now - this.lastFrame) / 1000, 0.05);
    this.lastDt = dt;
    this.lastFrame = now;
    const t = now / 1000;

    // Feelings arrive faster than they leave. Chase a new posture quickly
    // while the reaction is fresh, then settle — a flat rate in both
    // directions is what made her timing feel wrong.
    const age = (now - this.joltAt) / 700;
    const react = this.jolt * Math.max(0, 1 - age) ** 2;
    const k = 1 - Math.exp(-dt * (6 + react * 16));
    for (const key of Object.keys(this.now) as Array<keyof Feeling>) {
      this.now[key] += (this.want[key] - this.now[key]) * k;
    }

    const f = this.now;

    // Sentiment drifts slower than mood: weather, not a gust.
    this.sentiment += (this.sentimentTarget - this.sentiment) * (1 - Math.exp(-dt * 0.35));
    // Warmth pulls her hue toward gold and lifts saturation; bleakness cools
    // her toward indigo and drains it.
    const warmth = this.sentiment;
    const targetHue = f.hue + (warmth > 0 ? -warmth * 34 : warmth * 26);
    const targetSat = f.sat + warmth * 0.12;

    // COLOUR IS INTERPOLATED AS A POINT, NOT AS AN ANGLE.
    //
    // Taking the shortest way round the hue wheel sounds right and is wrong:
    // it makes her pass through every hue in between. Going delighted (gold,
    // 44) to singing (violet, 275) the short way is -129 degrees, which runs
    // 44 -> 0 -> 315 — straight through red. Measured on the real easing
    // curve, that is eleven frames of rgb(204,62,73) every time she started
    // to sing after a happy moment. She turned red mid-song.
    //
    // Treating (hue, saturation) as a vector and moving in a straight line
    // passes through the DESATURATED middle instead. Gold to violet fades
    // toward pale and comes up violet, which is both correct and prettier —
    // and no intermediate hue can ever appear that neither end asked for.
    // The glass rises when she starts searching and lowers when she stops.
    const wantsGlass = this.mood === "researching" ? 1 : 0;
    this.hold += (wantsGlass - this.hold) * (1 - Math.exp(-dt * 4.5));
    // And the wave goes up when she says hello. Faster than the glass: a
    // greeting that arrives late is not a greeting.
    const wantsWave = this.mood === "greeting" ? 1 : 0;
    this.wave += (wantsWave - this.wave) * (1 - Math.exp(-dt * 7));
    // The iris engages when she starts looking. It snaps in faster than it
    // relaxes out, the way a lens does.
    const wantsIris = this.mood === "looking" ? 1 : 0;
    if (wantsIris && this.iris < 0.02) this.irisAt = now;
    this.iris += (wantsIris - this.iris) * (1 - Math.exp(-dt * (wantsIris ? 9 : 3.5)));

    const rate = 1 - Math.exp(-dt * 2.2);
    const angle = (this.hue * Math.PI) / 180;
    const targetAngle = (targetHue * Math.PI) / 180;
    let x = Math.cos(angle) * this.sat;
    let y = Math.sin(angle) * this.sat;
    x += (Math.cos(targetAngle) * targetSat - x) * rate;
    y += (Math.sin(targetAngle) * targetSat - y) * rate;
    let radius = Math.hypot(x, y);
    // Nearly-opposite colours — gold to violet is the one that bit us — have
    // a straight line that still leans to one side of the wheel, so the trip
    // picks up a tint of whatever it passes. Ducking saturation while the
    // hue is far from home turns that into a clean fade through pale: the
    // colour drains, turns, and comes back up. The dip is proportional to how
    // far she still has to travel, so short shifts are untouched.
    const remaining = Math.abs(shortestHueStep(this.hue, targetHue));
    if (remaining > 40) {
      radius *= 1 - 0.75 * Math.min(1, (remaining - 40) / 100);
    }
    // Below a hair's width of saturation the angle is meaningless noise, so
    // hold the hue steady and let saturation alone carry the change.
    if (radius > 1e-4) {
      this.hue = ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
    }
    this.sat = Math.min(1, radius);

    // Where the walk has actually carried her, eased so she glides rather
    // than teleports between polls.
    this.travel += (this.travelTarget - this.travel) * (1 - Math.exp(-dt * 3.0));
    this.facing += (this.facingTarget - this.facing) * (1 - Math.exp(-dt * 3.4));

    // A stride bob locked to the real gait period, so her body rises and
    // falls once per tripod swap rather than on an invented rhythm.
    let stride = 0;
    let lean = 0;
    if (this.gait) {
      const elapsed = performance.now() - this.gait.startedAt;
      const phase = (elapsed / this.gait.cycleMs) % 1;
      stride = Math.sin(phase * Math.PI * 2) * this.radius * 0.09;
      if (this.gait.action === "walk") {
        // Forward comes toward you and back recedes: on a head-on view that
        // reads as approach and retreat far better than sliding does.
        if (this.gait.direction === "forward") lean = 0.10;
        else if (this.gait.direction === "backward") lean = -0.08;
      }
    }
    this.walkScale += ((1 + lean) - this.walkScale) * (1 - Math.exp(-dt * 2.4));

    const wantX = this.cx
      + Math.sin(t * Math.PI * f.swayRate) * this.radius * f.sway * 4
      + this.travel * this.radius * 1.5
      // A turn swings her body across before it settles.
      + this.facing * this.radius * 0.55;
    // The overshoot itself. Positive feelings pop upward, negative ones
    // recoil down. Kept small: at 0.42 a run of mood changes -- which is
    // exactly what a polled UI produces -- stacked into a visible shudder,
    // and a companion who twitches reads as nervous rather than alive.
    void react;   // the reaction lives in the posture terms above, not here
    // A REACTION CHANGES HER SHAPE, NOT HER ADDRESS.
    //
    // `recoil` displaced her whole body on every mood change. Settled, she
    // moves 0.65 px per frame; a mood change moved her up to 14 px in ONE
    // frame, and moods change several times per turn now that listening,
    // thinking, speaking and idle all arrive as events. That lurch, repeated,
    // is the jitter -- she reads as twitchy rather than alive. The overshoot
    // still happens, but it lands in stretch, reach and eyes, where an
    // animator would put it.
    const wantY = this.cy
      - this.radius * f.lift
      + Math.sin(t * Math.PI * f.bobRate) * this.radius * f.bob * 2
      + stride;

    // And a body cannot teleport. Whatever changes upstream -- a mood, a
    // walk, a resize -- she may cross at most this much of herself in one
    // frame. Scaled by elapsed time so it is a speed limit, not a frame
    // limit, and generous enough that a real walk is untouched.
    const limit = this.radius * 4.5 * dt;
    const fromX = this.bodyX || wantX;
    const fromY = this.bodyY || wantY;
    // Clamp the MOVEMENT, not each axis: limiting x and y separately lets a
    // diagonal travel root-two times the limit, which is the difference
    // between a speed limit and a bounding box.
    const dx = wantX - fromX;
    const dy = wantY - fromY;
    const distance = Math.hypot(dx, dy);
    if (distance <= limit || distance === 0) {
      this.bodyX = wantX;
      this.bodyY = wantY;
    } else {
      const scale = limit / distance;
      this.bodyX = fromX + dx * scale;
      this.bodyY = fromY + dy * scale;
    }

    this.stepTentacles(dt, t, f);
    this.updateGaze(now, t);
    this.updateBlink(now, f);
    this.draw(t, f);
  }

  /**
   * Verlet integration, then constraint relaxation.
   *
   * Each point keeps its previous position instead of a velocity; the
   * difference IS the velocity, so damping and collisions are one
   * subtraction. Then the chain is pulled back to its rest length a few
   * times per frame. Two passes is enough to look like a limb and cheap
   * enough to forget about.
   */
  private stepTentacles(dt: number, t: number, f: Feeling): void {
    const step = (this.radius * 1.55) / LINKS;
    for (const tentacle of this.tentacles) {
      const points = tentacle.points;

      // The root is pinned to her body, slightly inside it so the join is
      // hidden under the blob rather than floating beside it.
      const root = points[0];
      root.x = this.bodyX + Math.cos(tentacle.angle) * this.radius * 0.62;
      root.y = this.bodyY + Math.sin(tentacle.angle) * this.radius * 0.62;

      // A per-tentacle wander, phase-offset so they never move in unison.
      const wobble = Math.sin(t * Math.PI * f.wanderRate + tentacle.phase);
      let pushX = Math.cos(tentacle.angle) * f.reach + wobble * f.wander;
      let pushY = f.droop;
      // While she is searching, one limb holds the magnifying glass up beside
      // her head and sweeps it slowly, as though reading. `hold` eases with
      // the mood rather than snapping, so the glass rises and lowers with her.
      const index = this.tentacles.indexOf(tentacle);
      // WALKING. The lifted tripod swings forward and up; the planted tripod
      // pushes back and takes her weight. Six legs, two groups, alternating —
      // the gait her vendor bridge drives on the real robot.
      const lift = this.tripodLift(index);
      if (lift > 0) {
        const gait = this.gait;
        const push = gait && gait.direction === "backward" ? -1 : 1;
        pushY -= lift * 1.5;
        pushX += Math.cos(tentacle.angle) * lift * 0.5 * push;
      } else if (this.gait) {
        // Planted: braced outward, carrying her.
        pushY += 0.28;
      }
      // GREETING: one limb goes up and waves.
      //
      // `WAVING_TENTACLE` was declared, documented in the feeling table
      // ("One tentacle waves; see WAVING_TENTACLE below") and then used by
      // nothing at all -- so she has never once waved at anyone. Same defect
      // as the output guards that were defined and never called; eslint
      // caught this one.
      if (index === WAVING_TENTACLE && this.wave > 0.01) {
        const swing = Math.sin(t * 7.2) * 0.75;
        pushX = pushX * (1 - this.wave) + (0.85 + swing) * this.wave;
        pushY = pushY * (1 - this.wave) + -1.7 * this.wave;
      }
      if (index === MAGNIFIER_TENTACLE && this.hold > 0.01) {
        const sweep = Math.sin(t * 1.7) * 0.30;
        pushX = pushX * (1 - this.hold) + (0.62 + sweep) * this.hold;
        pushY = pushY * (1 - this.hold) + -1.45 * this.hold;
      }

      for (let i = 1; i < points.length; i += 1) {
        const p = points[i];
        // Damping must be per SECOND, not per frame. At 0.86 per frame a
        // 120 Hz display damped twice as hard as a 60 Hz one while the force
        // term below (which scales with dt) did not — so her limbs were
        // stiffer or looser depending on the monitor, and under-damped at
        // high refresh rates, which buzzes.
        const keep = Math.pow(0.86, dt * 60);
        const vx = (p.x - p.px) * keep;
        const vy = (p.y - p.py) * keep;
        p.px = p.x;
        p.py = p.y;
        // Force grows toward the tip, so the ends whip and the base holds.
        const weight = (i / (points.length - 1)) ** 1.4;
        p.x += vx + pushX * weight * this.radius * dt * 9;
        p.y += vy + pushY * weight * this.radius * dt * 9;
      }

      // Relax the chain back to its rest length.
      for (let pass = 0; pass < 2; pass += 1) {
        for (let i = 1; i < points.length; i += 1) {
          const a = points[i - 1];
          const b = points[i];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const distance = Math.hypot(dx, dy) || 1e-4;
          const correction = (distance - step) / distance;
          // The root never moves; the outer point takes the whole fix.
          const share = i === 1 ? 1 : 0.5;
          if (i > 1) {
            a.x += dx * correction * (1 - share);
            a.y += dy * correction * (1 - share);
          }
          b.x -= dx * correction * share;
          b.y -= dy * correction * share;
        }
      }
    }
  }

  /** Eyes that flick about. A fixed stare is the fastest way to look dead. */
  private updateGaze(now: number, t: number): void {
    if (now >= this.gaze.next) {
      // Smaller, rarer saccades. At +-0.45 of an eye every 0.7-2.9 s her eyes
      // darted, and darting eyes read as nerves rather than as life. Real
      // idle gaze is mostly still with an occasional glance.
      this.gaze.tx = (Math.random() - 0.5) * 0.5;
      this.gaze.ty = (Math.random() - 0.5) * 0.34;
      this.gaze.next = now + 1800 + Math.random() * 3600;
    }
    // Thinking looks up and away; it is the clearest single tell there is.
    // With a bubble overhead she looks AT it, drifting slightly as she works,
    // so the two read as one gesture rather than two coincidences.
    if (this.thinking) {
      this.gaze.tx = Math.sin(t * 0.45) * 0.26;
      this.gaze.ty = -0.82;
    } else if (this.mood === "thinking") {
      this.gaze.tx = Math.sin(t * 0.5) * 0.5;
      this.gaze.ty = -0.55;
    }
    // Time-based, so the eyes move at the same speed on any display. The old
    // per-frame 0.14 moved them twice as fast on a 120 Hz screen.
    const glide = 1 - Math.exp(-this.lastDt * 7);
    this.gaze.x += (this.gaze.tx - this.gaze.x) * glide;
    this.gaze.y += (this.gaze.ty - this.gaze.y) * glide;
  }

  private updateBlink(now: number, f: Feeling): void {
    if (now >= this.blinkAt) {
      this.blink = 1;
      // Irregular on purpose: a metronome blink reads as a machine.
      this.blinkAt = now + f.blinkEvery * 1000 * (0.55 + Math.random() * 0.9);
    }
    // Per second, not per frame: a blink lasted half as long on a 120 Hz
    // display, which is part of what made her look twitchy.
    this.blink = Math.max(0, this.blink - this.lastDt * 9.6);
  }

  /**
   * How open her mouth is right now, from the actual utterance.
   *
   * A raised cosine inside each beat, so the mouth arrives and leaves
   * smoothly instead of snapping between syllables; the gaps between beats
   * fall to zero on their own, which is what produces visible pauses.
   */
  private speechLevel(): number {
    const speech = this.speech;
    if (!speech) return 0;
    const elapsed = performance.now() - speech.startedAt;
    if (elapsed > speech.durationMs) {
      if (speech.openEnded) {
        // The estimate ran out but the audio has not stopped. Keep the mouth
        // quietly moving rather than freezing it shut mid-sentence; the real
        // `speech_end` decides when she is finished.
        const at = (elapsed / Math.max(1, speech.durationMs)) % 1;
        return 0.28 * (0.5 - 0.5 * Math.cos(at * Math.PI * 2));
      }
      // Known duration, and it elapsed: this phrase is genuinely done. Start
      // the next one immediately so a streamed reply runs without a seam.
      this.endSpeech();
      return this.speech ? this.speechLevel() : 0;
    }
    const at = (elapsed / speech.durationMs) * speech.total;
    for (const beat of speech.beats) {
      if (at < beat.start) break;                       // in a rest
      if (at <= beat.start + beat.span) {
        const phase = (at - beat.start) / beat.span;
        return beat.peak * (0.5 - 0.5 * Math.cos(phase * Math.PI * 2));
      }
    }
    return 0;
  }

  /** Her colour at a given lightness. One hue keeps her readable. */
  private shade(amount: number): string {
    const [r, g, b] = hsl(this.hue, this.sat, Math.max(0.06, Math.min(0.94, 0.52 * amount)));
    return `rgb(${r},${g},${b})`;
  }

  private draw(t: number, f: Feeling): void {
    const { ctx } = this;
    const w = this.canvas.clientWidth || 300;
    const h = this.canvas.clientHeight || 300;
    ctx.clearRect(0, 0, w, h);
    // Walking toward you makes her bigger and walking away makes her smaller.
    // On a head-on view that reads as depth, which sliding never does.
    const r = this.radius * this.walkScale;

    // A soft contact shadow. Without it she floats in a void; with it she
    // occupies a place.
    const shadowSquash = 1 - (this.bodyY - this.cy) / (r * 3);
    ctx.save();
    ctx.globalAlpha = 0.13 * Math.max(0.3, shadowSquash);
    ctx.fillStyle = "#3d4a44";
    ctx.beginPath();
    ctx.ellipse(this.cx, this.cy + r * 1.85, r * 1.15 * shadowSquash, r * 0.2, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // Tentacles behind the body, drawn as tapering strokes.
    for (const tentacle of this.tentacles) {
      const points = tentacle.points;
      for (let i = 1; i < points.length; i += 1) {
        const a = points[i - 1];
        const b = points[i];
        const taper = 1 - (i - 1) / (points.length - 1);
        ctx.beginPath();
        ctx.strokeStyle = this.shade(0.62 + taper * 0.18);
        ctx.lineWidth = Math.max(1.5, r * 0.30 * (0.35 + taper * 0.75));
        ctx.lineCap = "round";
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      // A little sucker-dot near the tip reads as detail at no cost.
      const tip = points[points.length - 1];
      ctx.fillStyle = this.shade(0.9);
      ctx.beginPath();
      ctx.arc(tip.x, tip.y, Math.max(1, r * 0.045), 0, Math.PI * 2);
      ctx.fill();
    }

    // The magnifying glass, in the limb that is holding it up.
    this.drawMagnifier(r, t);

    // The body: squash and stretch, the oldest trick in animation.
    const squash = f.stretch;
    ctx.save();
    ctx.translate(this.bodyX, this.bodyY);
    const gradient = ctx.createRadialGradient(-r * 0.3, -r * 0.4, r * 0.15, 0, 0, r * 1.25);
    gradient.addColorStop(0, this.shade(1.16));
    gradient.addColorStop(1, this.shade(0.82));
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.ellipse(0, 0, r / squash, r * squash, 0, 0, Math.PI * 2);
    ctx.fill();

    this.drawFace(ctx, r, f, t);
    ctx.restore();

    // Focus brackets: four corners that snap inward on to her, the way a
    // viewfinder acquires a subject. Drawn last so they sit over everything.
    if (this.iris > 0.02) {
      const since = (t * 1000 - this.irisAt) / 1000;
      const snap = Math.min(1, Math.max(0, since) / 0.45);
      const spread = r * (2.3 - 0.75 * snap);
      const arm = r * 0.34;
      ctx.save();
      ctx.globalAlpha = this.iris * 0.85;
      ctx.strokeStyle = "rgba(255,196,116,.9)";
      ctx.lineWidth = Math.max(1.5, r * 0.055);
      ctx.lineCap = "round";
      for (const sx of [-1, 1]) {
        for (const sy of [-1, 1]) {
          const x = this.bodyX + sx * spread;
          const y = this.bodyY + sy * spread * 0.82;
          ctx.beginPath();
          ctx.moveTo(x - sx * arm, y);
          ctx.lineTo(x, y);
          ctx.lineTo(x, y - sy * arm);
          ctx.stroke();
        }
      }
      ctx.restore();
    }

    // Last, so it sits over her: the thought she is having.
    this.drawThoughtBubble(t, r);
    if (this.deaf) this.drawDeafBadge(r);
  }

  /**
   * The thought she is having, drawn as an icon rather than a doodle.
   *
   * THREE RULES, because the first version broke all three:
   *
   *   It must not look like part of her. It was filled with her own body
   *   colour, so a green bubble on a green creature read as an growth rather
   *   than a thought. The bubble is now a pale card with a thin rim, the way
   *   a speech balloon has always been drawn.
   *
   *   It must not sit on her head. It overlapped her skull and clipped on the
   *   frame edge. It now clears her radius and is clamped inside the canvas.
   *
   *   The glyph must be recognisable in a quarter of a second, at this size,
   *   with no caption. A circle with a line under it is not an eye. These are
   *   drawn to icon-set discipline: one stroke weight, round caps and joins,
   *   built from the same primitives, so the three read as a family.
   *
   *     think     three dots, filling left to right
   *     research  a magnifying glass, sweeping
   *     sight     an eye, with an iris that tracks and a lid that blinks
   */
  private drawThoughtBubble(t: number, r: number): void {
    const { ctx } = this;
    const state = this.thinking;
    const now = performance.now();
    const target = state && now < state.until ? 1 : 0;
    this.bubble += (target - this.bubble) * (target > this.bubble ? 0.16 : 0.1);
    if (this.bubble < 0.01) { this.bubble = 0; return; }
    const grow = this.bubble;

    const br = r * 0.52 * grow;
    const w = this.canvas.clientWidth || 300;
    const margin = br + Math.max(3, r * 0.1);
    // Clear of her head, up and to the right, and always inside the frame.
    const bx = Math.max(margin, Math.min(w - margin, this.bodyX + r * 1.28));
    const by = Math.max(margin, this.bodyY - r * 1.34);

    ctx.save();
    ctx.globalAlpha = Math.min(1, grow);

    // The trail: two beads leading the eye from her head to the thought.
    const hx = this.bodyX + r * 0.52;
    const hy = this.bodyY - r * 0.66;
    for (let i = 0; i < 2; i += 1) {
      const k = 0.34 + i * 0.3;
      ctx.beginPath();
      ctx.arc(hx + (bx - hx) * k, hy + (by - hy) * k, br * (0.1 + i * 0.07), 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,255,255,0.94)";
      ctx.fill();
      ctx.strokeStyle = this.shade(0.7);
      ctx.lineWidth = Math.max(1, br * 0.05);
      ctx.stroke();
    }

    // The card. Pale, not her colour, with a soft drop so it floats.
    ctx.save();
    ctx.shadowColor = "rgba(20,30,34,0.18)";
    ctx.shadowBlur = br * 0.5;
    ctx.shadowOffsetY = br * 0.12;
    ctx.beginPath();
    ctx.arc(bx, by, br, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(252,253,253,0.97)";
    ctx.fill();
    ctx.restore();
    ctx.beginPath();
    ctx.arc(bx, by, br, 0, Math.PI * 2);
    ctx.strokeStyle = this.shade(0.72);
    ctx.lineWidth = Math.max(1, br * 0.07);
    ctx.stroke();

    // One ink, one stroke weight, for all three glyphs.
    const ink = "#22303a";
    const stroke = Math.max(1.4, br * 0.13);
    ctx.strokeStyle = ink;
    ctx.fillStyle = ink;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const age = (now - (state?.since ?? now)) / 1000;
    const mode = state?.mode ?? "think";

    if (mode === "research") {
      // A magnifying glass, tilted the way one is held, sweeping slowly as
      // though reading across something.
      const swing = Math.sin(age * 1.6) * 0.22;
      ctx.save();
      ctx.translate(bx, by);
      ctx.rotate(swing);
      const lens = br * 0.38;
      ctx.lineWidth = stroke;
      ctx.beginPath();
      ctx.arc(-br * 0.1, -br * 0.1, lens, 0, Math.PI * 2);
      ctx.stroke();
      // Handle, leaving the rim at 45 degrees so it reads as one object.
      const edge = lens + stroke * 0.1;
      ctx.beginPath();
      ctx.moveTo(-br * 0.1 + Math.cos(Math.PI / 4) * edge, -br * 0.1 + Math.sin(Math.PI / 4) * edge);
      ctx.lineTo(-br * 0.1 + Math.cos(Math.PI / 4) * (edge + br * 0.42),
                 -br * 0.1 + Math.sin(Math.PI / 4) * (edge + br * 0.42));
      ctx.stroke();
      // A highlight inside the lens: two short arcs, the classic shorthand.
      ctx.lineWidth = stroke * 0.62;
      ctx.globalAlpha = Math.min(1, grow) * 0.5;
      ctx.beginPath();
      ctx.arc(-br * 0.1, -br * 0.1, lens * 0.55, Math.PI * 1.05, Math.PI * 1.45);
      ctx.stroke();
      ctx.restore();
    } else if (mode === "sight") {
      // An eye: a real almond from two arcs, an iris that looks around, and
      // a lid that closes now and then. Nothing else she does looks like this.
      const halfW = br * 0.62;
      const halfH = br * 0.36;
      const blink = Math.max(0, Math.sin(age * 1.5 - 1.35));
      const open = 1 - blink * blink * 0.94;
      ctx.lineWidth = stroke;
      ctx.beginPath();
      ctx.moveTo(bx - halfW, by);
      ctx.quadraticCurveTo(bx, by - halfH * 2 * open, bx + halfW, by);
      ctx.quadraticCurveTo(bx, by + halfH * 2 * open, bx - halfW, by);
      ctx.stroke();
      if (open > 0.35) {
        const gaze = Math.sin(age * 0.9) * halfW * 0.3;
        ctx.save();
        // Clip so the iris can never spill outside the lid.
        ctx.beginPath();
        ctx.moveTo(bx - halfW, by);
        ctx.quadraticCurveTo(bx, by - halfH * 2 * open, bx + halfW, by);
        ctx.quadraticCurveTo(bx, by + halfH * 2 * open, bx - halfW, by);
        ctx.clip();
        ctx.beginPath();
        ctx.arc(bx + gaze, by, halfH * 0.92, 0, Math.PI * 2);
        ctx.lineWidth = stroke * 0.9;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(bx + gaze, by, halfH * 0.4, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
    } else {
      // Three dots that fill left to right and reset — a thought forming,
      // not a metronome.
      const step = (age * 1.25) % 1.35;
      for (let i = 0; i < 3; i += 1) {
        const lit = Math.max(0, Math.min(1, (step - i * 0.22) * 4));
        ctx.globalAlpha = Math.min(1, grow) * (0.24 + 0.76 * lit);
        ctx.beginPath();
        ctx.arc(bx + (i - 1) * br * 0.42, by, br * (0.1 + 0.05 * lit), 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  /** A struck-through ear: she cannot hear you, and it is not her fault. */
  private drawDeafBadge(r: number): void {
    const { ctx } = this;
    const x = this.bodyX + r * 1.15;
    const y = this.bodyY - r * 0.95;
    const s = r * 0.38;
    ctx.save();
    ctx.strokeStyle = "#B3453A";
    ctx.lineWidth = Math.max(2, s * 0.22);
    ctx.lineCap = "round";
    // An ear.
    ctx.beginPath();
    ctx.arc(x, y, s * 0.62, Math.PI * 1.15, Math.PI * 0.35);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x, y - s * 0.1, s * 0.26, Math.PI * 1.1, Math.PI * 0.5);
    ctx.stroke();
    // Struck through.
    ctx.beginPath();
    ctx.moveTo(x - s * 0.85, y + s * 0.85);
    ctx.lineTo(x + s * 0.85, y - s * 0.85);
    ctx.stroke();
    ctx.restore();
  }

  /**
   * A small magnifying glass, for when she is searching the world.
   *
   * Drawn rather than fetched — like everything else about her, it has no
   * asset and so cannot fail to load. It sits at the tip of the limb that
   * lifts during `researching` and tilts with the sweep, so it reads as held
   * rather than pasted on. A slow glint travels across the lens; that one
   * moving highlight is most of what makes it read as glass.
   */
  private drawMagnifier(r: number, t: number): void {
    if (this.hold < 0.02) return;
    const { ctx } = this;
    const tentacle = this.tentacles[MAGNIFIER_TENTACLE];
    if (!tentacle) return;
    const points = tentacle.points;
    const tip = points[points.length - 1];
    const before = points[points.length - 2];
    const grip = Math.atan2(tip.y - before.y, tip.x - before.x);

    const lens = r * 0.40 * this.hold;
    // The lens sits beyond the tip, along the limb, so the limb becomes the
    // handle rather than overlapping the glass.
    const cx = tip.x + Math.cos(grip) * lens * 0.95;
    const cy = tip.y + Math.sin(grip) * lens * 0.95;

    ctx.save();
    ctx.globalAlpha = Math.min(1, this.hold);

    // Handle: a short stub from the limb into the rim.
    ctx.strokeStyle = "#5c4632";
    ctx.lineCap = "round";
    ctx.lineWidth = Math.max(1.5, lens * 0.30);
    ctx.beginPath();
    ctx.moveTo(tip.x - Math.cos(grip) * lens * 0.55, tip.y - Math.sin(grip) * lens * 0.55);
    ctx.lineTo(cx - Math.cos(grip) * lens * 0.90, cy - Math.sin(grip) * lens * 0.90);
    ctx.stroke();

    // Glass: a pale disc, lighter than she is.
    ctx.beginPath();
    ctx.arc(cx, cy, lens, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(226,244,247,0.42)";
    ctx.fill();

    // Rim.
    ctx.beginPath();
    ctx.arc(cx, cy, lens, 0, Math.PI * 2);
    ctx.strokeStyle = this.shade(1.25);
    ctx.lineWidth = Math.max(1.5, lens * 0.20);
    ctx.stroke();

    // The glint: one short arc that travels around the lens.
    const glint = t * 1.1;
    ctx.beginPath();
    ctx.arc(cx, cy, lens * 0.62, glint, glint + 0.85);
    ctx.strokeStyle = "rgba(255,255,255,0.75)";
    ctx.lineWidth = Math.max(1, lens * 0.16);
    ctx.stroke();
    ctx.restore();
  }

  /**
   * The face carries the emotion.
   *
   * People read a character's feeling almost entirely from its eyes, so the
   * eyes are enormous, the pupils move, and delight closes them into happy
   * arcs. That single arc does more work than every tentacle combined.
   */
  private drawFace(ctx: CanvasRenderingContext2D, r: number, f: Feeling, t: number): void {
    const eyeR = r * 0.34;
    const eyeY = -r * 0.10;
    const open = Math.max(0.06, f.eyes * (1 - this.blink));
    const happy = f.smile > 0.7 && f.eyes < 0.7;   // eyes squeezed with joy

    for (const side of [-1, 1]) {
      const ex = side * r * 0.42;
      if (happy) {
        // ^ ^ — the whole read of delight, in two strokes.
        ctx.strokeStyle = "#26343a";
        ctx.lineWidth = r * 0.11;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.arc(ex, eyeY + eyeR * 0.25, eyeR * 0.78, Math.PI * 1.15, Math.PI * 1.85);
        ctx.stroke();
        continue;
      }
      ctx.fillStyle = "#fdfcf8";
      ctx.beginPath();
      ctx.ellipse(ex, eyeY, eyeR, eyeR * open, 0, 0, Math.PI * 2);
      ctx.fill();

      const pupilR = eyeR * f.pupil;
      ctx.fillStyle = "#26343a";
      ctx.beginPath();
      ctx.ellipse(
        ex + this.gaze.x * eyeR * 0.42,
        eyeY + this.gaze.y * eyeR * 0.42 * open,
        pupilR, pupilR * Math.max(0.12, open), 0, 0, Math.PI * 2,
      );
      ctx.fill();

      // WHEN SHE IS LOOKING, HER PUPIL IS A LENS.
      //
      // Six aperture blades close over the pupil and open again as focus
      // lands, and a ring pulses outward once at the moment it locks. It is
      // the same gesture a camera makes, which is exactly what she is doing —
      // her eye IS a camera — so it reads instantly without a caption.
      if (this.iris > 0.02) {
        const px = ex + this.gaze.x * eyeR * 0.42;
        const py = eyeY + this.gaze.y * eyeR * 0.42 * open;
        const since = (t * 1000 - this.irisAt) / 1000;
        // Close hard, then relax open: focus hunting, then landing.
        const hunt = Math.exp(-Math.max(0, since) * 2.2);
        const blades = pupilR * (1.55 - 0.75 * hunt) * this.iris;
        ctx.save();
        ctx.globalAlpha = this.iris;
        ctx.strokeStyle = "rgba(255,214,150,.95)";
        ctx.lineWidth = Math.max(1, eyeR * 0.07);
        ctx.lineJoin = "round";
        ctx.beginPath();
        for (let i = 0; i < 6; i += 1) {
          const a = (i / 6) * Math.PI * 2 + since * 0.6;
          const x = px + Math.cos(a) * blades;
          const y = py + Math.sin(a) * blades * Math.max(0.2, open);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.stroke();
        // The lock: one ring that expands and fades, once.
        if (since > 0.55 && since < 1.35) {
          const grow = (since - 0.55) / 0.8;
          ctx.globalAlpha = this.iris * (1 - grow);
          ctx.beginPath();
          ctx.arc(px, py, pupilR * (1.6 + grow * 1.5), 0, Math.PI * 2);
          ctx.stroke();
        }
        ctx.restore();
      }

      // A fixed catchlight. Eyes without one look like buttons.
      ctx.fillStyle = "rgba(255,255,255,.92)";
      ctx.beginPath();
      ctx.arc(ex - eyeR * 0.26, eyeY - eyeR * 0.3 * open, eyeR * 0.16, 0, Math.PI * 2);
      ctx.fill();
    }

    // Cheeks. Pure cuteness, costs two circles.
    if (f.smile > 0.45) {
      ctx.fillStyle = `rgba(240,150,160,${0.16 + f.smile * 0.2})`;
      for (const side of [-1, 1]) {
        ctx.beginPath();
        ctx.ellipse(side * r * 0.72, eyeY + r * 0.30, r * 0.17, r * 0.11, 0, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // The mouth. While she talks it flutters on two detuned sines, because
    // one sine is a metronome and speech never is.
    const chatter = this.speechLevel();
    const openness = f.mouthOpen * (0.35 + chatter * 0.75);
    const my = r * 0.42;
    ctx.strokeStyle = "#26343a";
    ctx.fillStyle = "#26343a";
    ctx.lineWidth = r * 0.085;
    ctx.lineCap = "round";
    if (openness > 0.18) {
      ctx.beginPath();
      ctx.ellipse(0, my, r * 0.20, r * 0.10 + r * 0.26 * openness, 0, 0, Math.PI * 2);
      ctx.fill();
    } else {
      const curve = f.smile * r * 0.22;
      ctx.beginPath();
      ctx.moveTo(-r * 0.20, my - curve * 0.4);
      ctx.quadraticCurveTo(0, my + curve, r * 0.20, my - curve * 0.4);
      ctx.stroke();
    }
  }
}

/** Degrees to turn to reach a hue, never the long way round the wheel. */
function shortestHueStep(from: number, to: number): number {
  return (((to - from) % 360) + 540) % 360 - 180;
}

/** HSL to RGB. Hue is the emotional axis, so colour is authored in it. */
function hsl(h: number, s: number, l: number): [number, number, number] {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = (((h % 360) + 360) % 360) / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  const [r, g, b] =
    hp < 1 ? [c, x, 0] : hp < 2 ? [x, c, 0] : hp < 3 ? [0, c, x] :
    hp < 4 ? [0, x, c] : hp < 5 ? [x, 0, c] : [c, 0, x];
  const m = l - c / 2;
  return [
    Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255),
  ];
}
