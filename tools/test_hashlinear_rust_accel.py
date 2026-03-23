#!/usr/bin/env python3
from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from unittest import mock


MODULE_NAME = "tools.hashlinear_light_runtime"


def _reload_runtime(env: dict[str, str] | None = None, *, fake_rust_module=None, import_error: Exception | None = None):
    original_env = os.environ.copy()
    try:
        for key in ["HASHLINEAR_USE_RUST"]:
            os.environ.pop(key, None)
        if env:
            os.environ.update(env)

        sys.modules.pop(MODULE_NAME, None)

        real_import_module = importlib.import_module

        def _fake_import_module(name: str, package: str | None = None):
            if name == "hashlinear_rust_ext":
                if import_error is not None:
                    raise import_error
                if fake_rust_module is not None:
                    return fake_rust_module
            return real_import_module(name, package)

        with mock.patch("importlib.import_module", side_effect=_fake_import_module):
            return importlib.import_module(MODULE_NAME)
    finally:
        os.environ.clear()
        os.environ.update(original_env)


class HashlinearRustAccelTests(unittest.TestCase):
    def test_fallback_when_rust_import_fails(self) -> None:
        runtime = _reload_runtime(import_error=ImportError("missing rust ext"))
        self.assertFalse(runtime.HAS_RUST_ACCEL)

        features = runtime._extract_features(
            "Hello  world",
            analyzer="char",
            ngram_range=(2, 4),
            n_features=128,
            alternate_sign=False,
            norm="l2",
            lowercase=True,
        )
        self.assertTrue(features)

    def test_env_disable_forces_python_fallback(self) -> None:
        fake_rust = types.SimpleNamespace(
            extract_features=lambda **kwargs: {1: 1.0},
        )
        runtime = _reload_runtime(env={"HASHLINEAR_USE_RUST": "0"}, fake_rust_module=fake_rust)
        self.assertFalse(runtime.HAS_RUST_ACCEL)

        features = runtime._extract_features(
            "Hello",
            analyzer="char",
            ngram_range=(2, 4),
            n_features=128,
            alternate_sign=False,
            norm=None,
            lowercase=True,
        )
        self.assertNotEqual(features, {1: 1.0})

    @unittest.skipUnless(importlib.util.find_spec("hashlinear_rust_ext") is not None, "Rust extension not built")
    def test_rust_import_available(self) -> None:
        runtime = _reload_runtime()
        self.assertTrue(runtime.HAS_RUST_ACCEL)

    @unittest.skipUnless(importlib.util.find_spec("hashlinear_rust_ext") is not None, "Rust extension not built")
    def test_rust_matches_python_samples(self) -> None:
        runtime = _reload_runtime()
        samples = [
            ("Hello  world", "char", (2, 4), 256, False, "l2", True),
            ("AbC xyz\t123", "char", (1, 3), 512, True, None, True),
            ("中文 mixed English words", "word", (1, 2), 512, False, "l2", True),
        ]
        for text, analyzer, ngram_range, n_features, alternate_sign, norm, lowercase in samples:
            py_features = runtime._extract_features_python(
                text,
                analyzer=analyzer,
                ngram_range=ngram_range,
                n_features=n_features,
                alternate_sign=alternate_sign,
                norm=norm,
                lowercase=lowercase,
            )
            rust_features = runtime._extract_features_rust(
                text,
                analyzer=analyzer,
                ngram_range=ngram_range,
                n_features=n_features,
                alternate_sign=alternate_sign,
                norm=norm,
                lowercase=lowercase,
            )
            self.assertEqual(set(py_features), set(rust_features))
            for idx, py_value in py_features.items():
                self.assertAlmostEqual(py_value, rust_features[idx], places=12)


if __name__ == "__main__":
    unittest.main()
