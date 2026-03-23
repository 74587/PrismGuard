#!/usr/bin/env python3
from __future__ import annotations

import array
import importlib
import json
import math
import os
import re
from pathlib import Path
from typing import Iterable

try:
    import mmh3
except ImportError:
    mmh3 = None


RUNTIME_VERSION = 1
_WHITE_SPACES = re.compile(r"\s\s+")
_RUST_ENV = os.getenv("HASHLINEAR_USE_RUST", "").strip().lower()
_RUST_DISABLED = _RUST_ENV in {"0", "false", "no", "off"}
_RUST_FORCED = _RUST_ENV in {"1", "true", "yes", "on"}
_RUST_BACKEND_LOGGED = False
_RUST_FAILURE_LOGGED = False
_RUST_ACCEL_PERMANENTLY_DISABLED = False

try:
    _hashlinear_rust_ext = None if _RUST_DISABLED else importlib.import_module("hashlinear_rust_ext")
    HAS_RUST_ACCEL = _hashlinear_rust_ext is not None
    _RUST_IMPORT_ERROR = None
except Exception as e:
    _hashlinear_rust_ext = None
    HAS_RUST_ACCEL = False
    _RUST_IMPORT_ERROR = e


def murmurhash3_x86_32(data: bytes, seed: int = 0) -> int:
    if mmh3 is not None:
        return int(mmh3.hash_from_buffer(data, seed=seed, signed=True))

    c1 = 0xCC9E2D51
    c2 = 0x1B873593
    length = len(data)
    h1 = seed & 0xFFFFFFFF
    rounded_end = length & 0xFFFFFFFC

    for i in range(0, rounded_end, 4):
        k1 = (
            data[i]
            | (data[i + 1] << 8)
            | (data[i + 2] << 16)
            | (data[i + 3] << 24)
        )
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFF

        h1 ^= k1
        h1 = ((h1 << 13) | (h1 >> 19)) & 0xFFFFFFFF
        h1 = (h1 * 5 + 0xE6546B64) & 0xFFFFFFFF

    k1 = 0
    tail_size = length & 3
    if tail_size == 3:
        k1 ^= data[rounded_end + 2] << 16
    if tail_size >= 2:
        k1 ^= data[rounded_end + 1] << 8
    if tail_size >= 1:
        k1 ^= data[rounded_end]
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1

    h1 ^= length
    h1 ^= h1 >> 16
    h1 = (h1 * 0x85EBCA6B) & 0xFFFFFFFF
    h1 ^= h1 >> 13
    h1 = (h1 * 0xC2B2AE35) & 0xFFFFFFFF
    h1 ^= h1 >> 16

    if h1 & 0x80000000:
        return -((~h1 + 1) & 0xFFFFFFFF)
    return h1


def _preprocess(text: str, lowercase: bool) -> str:
    if lowercase:
        text = text.lower()
    return text


def iter_char_ngrams(text: str, ngram_range: tuple[int, int]) -> Iterable[str]:
    text = _WHITE_SPACES.sub(" ", text)
    text_len = len(text)
    min_n, max_n = ngram_range
    if min_n == 1:
        for ch in text:
            yield ch
        min_n += 1
    for n in range(min_n, min(max_n + 1, text_len + 1)):
        for i in range(text_len - n + 1):
            yield text[i : i + n]


def iter_word_ngrams(text: str, ngram_range: tuple[int, int]) -> Iterable[str]:
    tokens = text.split()
    min_n, max_n = ngram_range
    if max_n == 1:
        for tok in tokens:
            yield tok
        return
    original_tokens = tokens
    if min_n == 1:
        for tok in original_tokens:
            yield tok
        min_n += 1
    for n in range(min_n, min(max_n + 1, len(original_tokens) + 1)):
        for i in range(len(original_tokens) - n + 1):
            yield " ".join(original_tokens[i : i + n])


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _log_backend_once(backend: str, detail: str | None = None) -> None:
    global _RUST_BACKEND_LOGGED
    if _RUST_BACKEND_LOGGED:
        return
    suffix = f" ({detail})" if detail else ""
    print(f"[HashLinear] 特征提取使用{backend}{suffix}")
    _RUST_BACKEND_LOGGED = True


def _extract_features_python(
    text: str,
    *,
    analyzer: str,
    ngram_range: tuple[int, int],
    n_features: int,
    alternate_sign: bool,
    norm: str | None,
    lowercase: bool,
) -> dict[int, float]:
    text = _preprocess(text.replace("\r", " ").replace("\n", " "), lowercase)
    if analyzer == "char":
        grams = iter_char_ngrams(text, ngram_range)
    elif analyzer == "word":
        grams = iter_word_ngrams(text, ngram_range)
    else:
        raise ValueError(f"Unsupported analyzer: {analyzer}")

    counts: dict[int, float] = {}
    for gram in grams:
        h = murmurhash3_x86_32(gram.encode("utf-8"), seed=0)
        idx = abs(h) % n_features
        sign = -1.0 if (alternate_sign and h < 0) else 1.0
        counts[idx] = counts.get(idx, 0.0) + sign

    if norm == "l2" and counts:
        l2 = math.sqrt(sum(v * v for v in counts.values()))
        if l2 > 0:
            for idx, value in list(counts.items()):
                counts[idx] = value / l2

    return counts


def _extract_features_rust(
    text: str,
    *,
    analyzer: str,
    ngram_range: tuple[int, int],
    n_features: int,
    alternate_sign: bool,
    norm: str | None,
    lowercase: bool,
) -> dict[int, float]:
    if not HAS_RUST_ACCEL or _hashlinear_rust_ext is None:
        raise RuntimeError("Rust extension unavailable")
    return dict(
        _hashlinear_rust_ext.extract_features(
            text=text,
            analyzer=analyzer,
            ngram_range=ngram_range,
            n_features=n_features,
            alternate_sign=alternate_sign,
            norm=norm,
            lowercase=lowercase,
        )
    )


def _extract_features(
    text: str,
    *,
    analyzer: str,
    ngram_range: tuple[int, int],
    n_features: int,
    alternate_sign: bool,
    norm: str | None,
    lowercase: bool,
) -> dict[int, float]:
    global _RUST_FAILURE_LOGGED, _RUST_ACCEL_PERMANENTLY_DISABLED

    if _RUST_DISABLED:
        _log_backend_once("Python fallback", "HASHLINEAR_USE_RUST=0")
        return _extract_features_python(
            text,
            analyzer=analyzer,
            ngram_range=ngram_range,
            n_features=n_features,
            alternate_sign=alternate_sign,
            norm=norm,
            lowercase=lowercase,
        )

    if not _RUST_ACCEL_PERMANENTLY_DISABLED and HAS_RUST_ACCEL:
        try:
            features = _extract_features_rust(
                text,
                analyzer=analyzer,
                ngram_range=ngram_range,
                n_features=n_features,
                alternate_sign=alternate_sign,
                norm=norm,
                lowercase=lowercase,
            )
            _log_backend_once("Rust 扩展")
            return features
        except Exception as e:
            _RUST_ACCEL_PERMANENTLY_DISABLED = True
            if not _RUST_FAILURE_LOGGED:
                print(f"[HashLinear] Rust 特征提取失败，回退 Python: {e}")
                _RUST_FAILURE_LOGGED = True

    if _RUST_FORCED and not HAS_RUST_ACCEL and not _RUST_FAILURE_LOGGED:
        detail = _RUST_IMPORT_ERROR or "extension not built"
        print(f"[HashLinear] HASHLINEAR_USE_RUST=1 但 Rust 扩展不可用，回退 Python: {detail}")
        _RUST_FAILURE_LOGGED = True

    if not HAS_RUST_ACCEL and _RUST_IMPORT_ERROR is not None and _RUST_FORCED:
        _log_backend_once("Python fallback", "Rust import failed")
    elif not HAS_RUST_ACCEL:
        _log_backend_once("Python fallback")
    else:
        _log_backend_once("Python fallback", "Rust disabled after failure")

    return _extract_features_python(
        text,
        analyzer=analyzer,
        ngram_range=ngram_range,
        n_features=n_features,
        alternate_sign=alternate_sign,
        norm=norm,
        lowercase=lowercase,
    )


class HashlinearRuntime:
    def __init__(self, metadata: dict, coef: array.array):
        runtime_version = int(metadata.get("runtime_version", 0))
        if runtime_version != RUNTIME_VERSION:
            raise RuntimeError(
                f"Unsupported hashlinear runtime version: {runtime_version} != {RUNTIME_VERSION}"
            )

        self.metadata = metadata
        self.cfg = metadata["cfg"]
        self.coef = coef
        self.intercept = float(metadata["intercept"])
        self.n_features = int(metadata["n_features"])
        self.alternate_sign = bool(self.cfg.get("alternate_sign", False))
        self.norm = self.cfg.get("norm")
        self.analyzer = self.cfg.get("analyzer", "char")
        self.ngram_range = tuple(self.cfg.get("ngram_range", [2, 4]))
        self.lowercase = bool(self.cfg.get("lowercase", True))

        if len(self.coef) != self.n_features:
            raise RuntimeError(
                f"Hashlinear runtime coef length mismatch: {len(self.coef)} != {self.n_features}"
            )

    @classmethod
    def load(cls, prefix: str | Path) -> "HashlinearRuntime":
        prefix = Path(prefix)
        meta = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
        coef = array.array("f")
        with open(prefix.with_suffix(".coef.f32"), "rb") as f:
            coef.frombytes(f.read())
        return cls(meta, coef)

    def _iter_features(self, text: str) -> Iterable[tuple[int, float]]:
        counts = _extract_features(
            text,
            analyzer=self.analyzer,
            ngram_range=self.ngram_range,
            n_features=self.n_features,
            alternate_sign=self.alternate_sign,
            norm=self.norm,
            lowercase=self.lowercase,
        )
        return counts.items()

    def decision_function(self, text: str) -> float:
        score = self.intercept
        for idx, value in self._iter_features(text):
            score += self.coef[idx] * value
        return score

    def predict_proba(self, text: str) -> float:
        return sigmoid(self.decision_function(text))
