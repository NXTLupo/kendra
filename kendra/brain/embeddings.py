from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Protocol

import numpy as np


class EmbeddingProvider(Protocol):
    dimensions: int

    def encode(self, text: str) -> np.ndarray: ...


class HashingEmbeddingProvider:
    """Zero-download local fallback embedding.

    This is intentionally simple: it is a signed hashing bag-of-words vector so
    Kendra's brain remains operational even before MiniLM is installed. It is not
    a substitute for a semantic model, but it gives stable similarity scoring and
    never reaches the network.
    """

    TOKEN_RE = re.compile(r"[A-Za-z0-9_'-]+")

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def encode(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimensions, dtype=np.float32)
        tokens = [token.lower() for token in self.TOKEN_RE.findall(text)]
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "little") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector /= norm
        return vector


class OnnxMiniLMEmbeddingProvider:
    """Real semantic embeddings on CPU via onnxruntime.

    all-MiniLM-L6-v2 exported to ONNX (~90 MB) with mean pooling and L2
    normalization. Chosen over the sentence-transformers provider because it
    needs no PyTorch: onnxruntime and tokenizers both publish small wheels
    for x86_64 macOS and Linux aarch64, so the identical stack runs on the
    Intel iMac and the Raspberry Pi body. Fully offline after provisioning.
    """

    def __init__(self, model_dir: Path):
        model_path = model_dir / "model.onnx"
        tokenizer_path = model_dir / "tokenizer.json"
        if not model_path.exists() or not tokenizer_path.exists():
            raise FileNotFoundError(
                f"Embedding model is not local: {model_dir}. Run "
                "scripts/fetch_local_models.py --embeddings; Kendra will not "
                "download it at runtime."
            )
        import onnxruntime
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=256)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = onnxruntime.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"]
        )
        self.dimensions = 384

    def encode(self, text: str) -> np.ndarray:
        encoding = self.tokenizer.encode(text or " ")
        ids = np.asarray([encoding.ids], dtype=np.int64)
        mask = np.asarray([encoding.attention_mask], dtype=np.int64)
        types = np.asarray([encoding.type_ids], dtype=np.int64)
        (hidden,) = self.session.run(
            None,
            {"input_ids": ids, "attention_mask": mask, "token_type_ids": types},
        )[:1]
        weights = mask[0].astype(np.float32)[:, None]
        pooled = (hidden[0] * weights).sum(axis=0) / max(float(weights.sum()), 1.0)
        vector = pooled.astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector /= norm
        return vector


class Qwen3OnnxEmbeddingProvider:
    """Qwen3-Embedding-0.6B int8 via onnxruntime — her upgraded semantic memory.

    A/B on Kendra's real memory corpus (2026-08-18): identical encode latency
    to MiniLM (~22 ms/item int8, 4 threads) with clearly better cross-phrasing
    retrieval ("what did Kendra see" -> an actual sight memory, where MiniLM
    returned a bare name row). 1024-dim, last-token pooling, instruction-
    prefixed queries per the model card. Apache 2.0; same onnxruntime stack
    as her ears, x86_64 macOS and Linux aarch64. On the 8 GB Pi prefer the
    model_q4.onnx variant to save ~300 MB resident.
    """

    QUERY_INSTRUCTION = "Instruct: Given a personal memory search, retrieve relevant memories\nQuery: "

    def __init__(self, model_dir: Path):
        import json as _json

        model_path = None
        for name in ("model_int8.onnx", "model_q4.onnx", "model.onnx"):
            if (model_dir / name).is_file():
                model_path = model_dir / name
                break
        tokenizer_path = model_dir / "tokenizer.json"
        config_path = model_dir / "config.json"
        if model_path is None or not tokenizer_path.is_file() or not config_path.is_file():
            raise FileNotFoundError(
                f"Embedding model is not local: {model_dir}. Run "
                "scripts/fetch_local_models.py --embeddings."
            )
        import onnxruntime
        from tokenizers import Tokenizer

        config = _json.loads(config_path.read_text())
        self._n_layers = int(config["num_hidden_layers"])
        self._n_kv = int(config["num_key_value_heads"])
        self._head_dim = int(config.get("head_dim") or config["hidden_size"] // config["num_attention_heads"])
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 4
        self.session = onnxruntime.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"]
        )
        self.dimensions = int(config["hidden_size"])

    def _run(self, text: str) -> np.ndarray:
        ids = self.tokenizer.encode(text or " ").ids[-512:]
        n = len(ids)
        feed = {
            "input_ids": np.asarray([ids], dtype=np.int64),
            "attention_mask": np.ones((1, n), dtype=np.int64),
            "position_ids": np.arange(n, dtype=np.int64)[None, :],
        }
        empty = np.zeros((1, self._n_kv, 0, self._head_dim), dtype=np.float32)
        for i in range(self._n_layers):
            feed[f"past_key_values.{i}.key"] = empty
            feed[f"past_key_values.{i}.value"] = empty
        hidden = self.session.run(["last_hidden_state"], feed)[0]
        vector = hidden[0, -1, :].astype(np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else vector

    def encode(self, text: str) -> np.ndarray:
        return self._run(text)

    def encode_query(self, text: str) -> np.ndarray:
        return self._run(self.QUERY_INSTRUCTION + (text or " "))


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_path: Path):
        if not model_path.exists():
            raise FileNotFoundError(
                f"Embedding model is not local: {model_path}. Kendra will not download it at runtime."
            )
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(str(model_path), local_files_only=True)
        probe = self.model.encode(["dimension probe"], normalize_embeddings=True)[0]
        self.dimensions = int(len(probe))

    def encode(self, text: str) -> np.ndarray:
        value = self.model.encode([text], normalize_embeddings=True)[0]
        return np.asarray(value, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = math.sqrt(float(np.dot(a, a))) * math.sqrt(float(np.dot(b, b)))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
