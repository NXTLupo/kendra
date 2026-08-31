export {};

/** Her live state, as her voice service publishes it on the face bus. */
export type KendraFaceEvent = {
  /** listening | thinking | speech_start | speech_end | idle */
  event: string;
  at: number;
  data: {
    /** speech_start: the words she is saying, right now */
    text?: string;
    /** speech_start: the utterance's true length; 0 when not yet known */
    seconds?: number;
    /** thinking: think | search | look */
    mode?: string;
    /**
     * speech_start: speech | song | hum | tune.
     *
     * A hum or a tune carries no text, so her mouth cannot be driven from
     * syllables — it needs a sustained envelope instead. Without this she
     * sang every song with her mouth shut.
     */
    kind?: string;
    /** gait: walk | turn */
    action?: string;
    /** gait: forward | backward | left | right */
    direction?: string;
    /** gait: how many gait cycles her body service committed to */
    cycles?: number;
    /** gait: the vendor's real tripod period, 0.4 s */
    cycle_seconds?: number;
    /** gait: clamped speed the body service used */
    speed?: number;
    /** gait: turn magnitude */
    degrees?: number;
  };
};

declare global {
  interface Window {
    kendra?: {
      request<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T>;
      /**
       * Subscribe to her live state; returns an unsubscribe function.
       *
       * Optional because the renderer also runs in a plain browser during
       * `vite dev`, where there is no preload bridge and she simply does not
       * animate to speech.
       */
      onEvent?(handler: (message: KendraFaceEvent) => void): () => void;
      platform: string;
    };
  }
}
