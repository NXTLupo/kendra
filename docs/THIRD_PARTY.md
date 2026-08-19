# Third-party runtime components

Kendra's repository code does not vendor the large external model/runtime projects. Provisioning scripts download or clone them into ignored local directories.

Before a production freeze, record the exact version/commit, model hash, and applicable license in `manifests/`.

Major components include:

- llama.cpp — local GGUF inference runtime
- Qwen3-4B GGUF — local text model
- whisper.cpp + base.en model — local speech recognition
- Piper + selected voice model — local speech synthesis; voice-model license is audited separately
- Vosk small English model — local constrained wake-phrase recognition
- openWakeWord — optional swappable wake provider; installed separately with
  `.[openwakeword]` and not required by the default Vosk voice stack
- OpenCV YuNet and SFace — local face detection/recognition
- Webots — desktop robot simulation
- SearXNG — optional local search aggregator for online research
- Kiwix/ZIM archives — optional offline reference library

No hosted inference subscription is required by the Kendra architecture.
