"""Offline Khmer translation with NLLB-200 (distilled 600M) via CTranslate2.

Source text (Korean or English) -> Khmer. Runs locally on CPU, no internet.
Model lives at ~/ManhwaPrep/models/nllb-600m-ct2 (CTranslate2 int8) with its
tokenizer files alongside.
"""

from __future__ import annotations

import os

NLLB_DIR = os.path.expanduser("~/ManhwaPrep/models/nllb-600m-ct2")

# FLORES-200 language codes
SRC_LANG = {"ko": "kor_Hang", "en": "eng_Latn"}
TGT_LANG = "khm_Khmr"


class KhmerTranslator:
    def __init__(self, src: str = "ko", model_dir: str | None = None):
        if src not in SRC_LANG:
            raise ValueError(f"Unsupported source language: {src}")
        self.model_dir = model_dir or NLLB_DIR
        if not os.path.isdir(self.model_dir):
            raise FileNotFoundError(
                f"NLLB model not found at {self.model_dir}. Run the converter "
                "(see README) to create it."
            )
        import ctranslate2
        import transformers

        self.src_lang = SRC_LANG[src]
        self.translator = ctranslate2.Translator(
            self.model_dir, device="cpu", compute_type="int8"
        )
        self.tok = transformers.AutoTokenizer.from_pretrained(self.model_dir)

    def translate(self, texts: list[str], batch_size: int = 16) -> list[str]:
        """Translate a list of source strings to Khmer (order preserved)."""
        if not texts:
            return []
        self.tok.src_lang = self.src_lang
        sources = [
            self.tok.convert_ids_to_tokens(self.tok.encode(t)) for t in texts
        ]
        results = self.translator.translate_batch(
            sources,
            target_prefix=[[TGT_LANG]] * len(sources),
            beam_size=4,
            max_batch_size=batch_size,
        )
        out = []
        for r in results:
            toks = r.hypotheses[0]
            if toks and toks[0] == TGT_LANG:
                toks = toks[1:]
            out.append(self.tok.decode(self.tok.convert_tokens_to_ids(toks)))
        return out
