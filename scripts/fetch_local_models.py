#!/usr/bin/env python3
"""Download Kendra's public local-runtime model assets into the repository.

No API key is required. The downloads are ordinary public HTTPS files. Large
language/vision models are opt-in because they are multi-gigabyte artifacts.
The runtime never sends inference data to these hosts; downloads are only a
one-time provisioning step.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Asset:
    name: str
    url: str
    destination: Path
    sha256: str | None = None
    unzip_to: Path | None = None


ASSETS = {
    "llm": Asset(
        "Qwen3-1.7B Q8_0 (Pi/iMac parity brain)",
        "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q8_0.gguf?download=true",
        ROOT / "models/qwen3-1.7b/Qwen3-1.7B-Q8_0.gguf",
        "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a",
    ),
    "llm_light": Asset(
        "Qwen3-0.6B Q8_0 (retired brain; A/B baseline only)",
        "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf?download=true",
        ROOT / "models/qwen3-0.6b/Qwen3-0.6B-Q8_0.gguf",
        "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031",
    ),
    "llm_deep": Asset(
        "Qwen3-4B Q4_K_M",
        "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf?download=true",
        ROOT / "models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf",
        "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
    ),
    "vlm": Asset(
        "Moondream2 text model f16 (Pi/iMac parity eyes; quantize locally to Q4_K_M)",
        "https://huggingface.co/ggml-org/moondream2-20250414-GGUF/resolve/main/moondream2-text-model-f16_ct-vicuna.gguf?download=true",
        ROOT / "models/moondream2/moondream2-text-model-f16.gguf",
        "925bcb666baf69ed747e26121af287b16ae7764483be9548b1382f29783689a5",
    ),
    "vlm_mmproj": Asset(
        "Moondream2 vision projector f16",
        "https://huggingface.co/ggml-org/moondream2-20250414-GGUF/resolve/main/moondream2-mmproj-f16-20250414.gguf?download=true",
        ROOT / "models/moondream2/moondream2-mmproj-f16.gguf",
        "4cc1cb3660d87ff56432ebeb7884ad35d67c48c7b9f6b2856f305e39c38eed8f",
    ),
    "whisper": Asset(
        "whisper.cpp small.en (accuracy tier for room microphones)",
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin?download=true",
        ROOT / "models/whisper/ggml-small.en.bin",
        "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d",
    ),
    "piper_voice": Asset(
        "Piper Amy medium voice",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx?download=true",
        ROOT / "models/piper/en_US-amy-medium/en_US-amy-medium.onnx",
        "b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18",
    ),
    "piper_config": Asset(
        "Piper Amy voice config",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json?download=true",
        ROOT / "models/piper/en_US-amy-medium/en_US-amy-medium.onnx.json",
        "95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77",
    ),
    "embeddings_qwen3": Asset(
        "Qwen3-Embedding-0.6B int8 ONNX (semantic memory, 1024-dim)",
        "https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/main/onnx/model_int8.onnx?download=true",
        ROOT / "models/embeddings/qwen3-embedding-0.6b/model_int8.onnx",
        "6d0ea863f78b4a84afa3c7fcba1ec341572b5e28121aef77b7092b1dfdf679c7",
    ),
    "embeddings_qwen3_tokenizer": Asset(
        "Qwen3-Embedding tokenizer",
        "https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/main/tokenizer.json?download=true",
        ROOT / "models/embeddings/qwen3-embedding-0.6b/tokenizer.json",
        None,
    ),
    "embeddings_qwen3_config": Asset(
        "Qwen3-Embedding config",
        "https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/main/config.json?download=true",
        ROOT / "models/embeddings/qwen3-embedding-0.6b/config.json",
        None,
    ),
    "embeddings_model": Asset(
        "all-MiniLM-L6-v2 ONNX semantic embeddings",
        "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx?download=true",
        ROOT / "models/embeddings/all-MiniLM-L6-v2/model.onnx",
        "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    ),
    "embeddings_tokenizer": Asset(
        "all-MiniLM-L6-v2 tokenizer",
        "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/tokenizer.json?download=true",
        ROOT / "models/embeddings/all-MiniLM-L6-v2/tokenizer.json",
        "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
    ),
    "vosk": Asset(
        "Vosk small US English",
        "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        ROOT / "models/vosk/vosk-model-small-en-us-0.15.zip",
        "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498",
        unzip_to=ROOT / "models/vosk",
    ),
    "yunet": Asset(
        "OpenCV YuNet face detector",
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        ROOT / "models/vision/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "sface": Asset(
        "OpenCV SFace face recognizer",
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        ROOT / "models/vision/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(asset: Asset, *, force: bool = False) -> None:
    target = asset.destination
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0 and not force:
        print(f"[skip] {asset.name}: {target}")
    else:
        temp = target.with_suffix(target.suffix + ".part")
        if temp.exists():
            temp.unlink()
        print(f"[download] {asset.name}\n  {asset.url}\n  -> {target}")
        request = urllib.request.Request(asset.url, headers={"User-Agent": "KendraModelProvisioner/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as output:
            total = response.headers.get("Content-Length")
            expected = int(total) if total and total.isdigit() else None
            read = 0
            next_report = 64 * 1024 * 1024
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                read += len(block)
                if expected and (read >= next_report or read == expected):
                    print(f"\r  {read / (1024**2):.1f}/{expected / (1024**2):.1f} MiB", end="", flush=True)
                    next_report += 64 * 1024 * 1024
            if expected:
                print()
        if expected is not None and read != expected:
            temp.unlink(missing_ok=True)
            raise RuntimeError(
                f"Incomplete download for {asset.name}: expected {expected} bytes, received {read}"
            )
        os.replace(temp, target)
    if asset.sha256:
        actual = sha256(target)
        if actual.lower() != asset.sha256.lower():
            raise RuntimeError(f"Checksum mismatch for {target}: expected {asset.sha256}, got {actual}")
    print(f"  sha256={sha256(target)}")
    if asset.unzip_to:
        marker = asset.unzip_to / target.stem
        if marker.exists() and not force:
            print(f"[skip] extracted: {marker}")
        else:
            print(f"[extract] {target} -> {asset.unzip_to}")
            asset.unzip_to.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(target) as archive:
                for member in archive.infolist():
                    resolved = (asset.unzip_to / member.filename).resolve()
                    if asset.unzip_to.resolve() not in resolved.parents and resolved != asset.unzip_to.resolve():
                        raise RuntimeError(f"Unsafe zip member: {member.filename}")
                archive.extractall(asset.unzip_to)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", action="store_true", help="Download LLM, ASR, TTS, wake-word, and face models")
    parser.add_argument("--llm", action="store_true", help="Download the Pi/iMac parity text brain")
    parser.add_argument("--deep-llm", action="store_true", help="Download the optional slower 4B text brain")
    parser.add_argument("--vlm", action="store_true")
    parser.add_argument("--voice", action="store_true")
    parser.add_argument("--vision", action="store_true")
    parser.add_argument("--embeddings", action="store_true", help="Semantic memory embeddings (MiniLM ONNX)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    names: list[str] = []
    if args.core:
        names += ["llm", "whisper", "piper_voice", "piper_config", "vosk", "yunet", "sface", "embeddings_model", "embeddings_tokenizer"]
    if args.llm:
        names.append("llm")
    if args.deep_llm:
        names.append("llm_deep")
    if args.vlm:
        names += ["vlm", "vlm_mmproj"]
    if args.voice:
        names += ["whisper", "piper_voice", "piper_config", "vosk"]
    if args.vision:
        names += ["yunet", "sface"]
    if args.embeddings:
        names += ["embeddings_qwen3", "embeddings_qwen3_tokenizer", "embeddings_qwen3_config", "embeddings_model", "embeddings_tokenizer"]
    if not names:
        parser.error("Choose --core, --llm, --deep-llm, --vlm, --voice, or --vision")

    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        download(ASSETS[name], force=args.force)
    print("Model provisioning complete. Record the printed SHA-256 values in manifests/models.yaml before production freeze.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
