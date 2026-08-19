# Kendra native desktop

This is the private iMac control room for Virtual Kendra. It is an Electron/Vite desktop app, not a hosted website. Its sandboxed renderer can invoke only an explicit set of named commands through Electron IPC. Electron passes those commands to a local Python child process over stdin/stdout; there is no dashboard HTTP API.

It presents service, model, reflex, body, and Webots state; local text and voice chat; iMac microphone and webcam access; conversations and cognitive events; Second Brain search/backup/import/encrypted SSH sync; and the signed Git intelligence channel.

The animated presence uses the owner-supplied Kendra reference at `public/kendra-reference.png`. The 3D body profile remains `config/webots.yaml`.

Production build and test:

```bash
npm ci
npm run build
npm test
npm run lint
```

The repository launcher starts the Python services and then opens this native window.
