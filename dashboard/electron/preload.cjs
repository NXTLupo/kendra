const { contextBridge, ipcRenderer } = require("electron");

// Named events only. The renderer receives her live state -- listening,
// thinking, speaking -- and cannot reach anything else in the main process.
const EVENT_CHANNEL = "kendra:event";

contextBridge.exposeInMainWorld("kendra", Object.freeze({
  request(method, params = {}) {
    return ipcRenderer.invoke("kendra:request", method, params);
  },
  /**
   * Subscribe to Kendra's live state. Returns an unsubscribe function.
   *
   * This is how her face learns she has started speaking. Before it existed
   * the renderer polled a transcript every three seconds and animated replies
   * that had already finished playing.
   */
  onEvent(handler) {
    if (typeof handler !== "function") return () => {};
    const listener = (_event, message) => {
      // Copy across the context bridge as plain data; never hand the renderer
      // an object it could use to reach back into Electron.
      try {
        handler({
          event: String(message?.event || ""),
          at: Number(message?.at) || 0,
          data: JSON.parse(JSON.stringify(message?.data ?? {})),
        });
      } catch {
        // A malformed event must never break the animation loop.
      }
    };
    ipcRenderer.on(EVENT_CHANNEL, listener);
    return () => ipcRenderer.removeListener(EVENT_CHANNEL, listener);
  },
  platform: "macOS",
}));
