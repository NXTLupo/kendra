const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("kendra", Object.freeze({
  request(method, params = {}) {
    return ipcRenderer.invoke("kendra:request", method, params);
  },
  platform: "macOS",
}));
