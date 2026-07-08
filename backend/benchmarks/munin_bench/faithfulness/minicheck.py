"""MiniCheck faithfulness judge (Track B, EVAL-SUITE-MASTER-PLAN sec 3).

Local, privacy-preserving fact-checker: MiniCheck-Flan-T5-Large predicts
support(document, claim) in [0, 1] at the sentence level (Tang et al. 2024,
arXiv 2404.10774). We replicate the model's OWN canonical inference (from the
repo's ``minicheck_web/inference.py``) rather than depend on the ``minicheck``
pip package, which pins older transformers:

- input string per (chunk, claim): ``"predict: " + chunk + <eos> + claim``
- the document is sentence-split and grouped into ~500-word chunks; each
  (chunk, claim) is scored and the claim's support = MAX over its chunks
- the seq2seq model is decoded ONE step (decoder_input_ids = 0); the support
  probability is ``softmax(logits[:, [3, 209]])[:, 1]`` (token 3 = "no support",
  token 209 = "support", per the MiniCheck authors' hardcoding).

``score_answer`` splits an answer into claims (sentences) and scores each against
the concatenated retrieved contexts, then aggregates to the Track B answer-level
metrics (mean support, fully-supported, any-unsupported).
"""

from __future__ import annotations

import re
from typing import Optional

from .. import clients, config

# The two Flan-T5 vocab ids MiniCheck reads its binary label from. Hardcoded in
# the authors' inference code; keep in lockstep with the model.
_NO_SUPPORT_ID = 3
_SUPPORT_ID = 209

# Lightweight sentence splitter (nltk/punkt is not a bench dep). Splits on
# sentence-final punctuation followed by whitespace + a capital/quote/digit.
# Good enough for both doc chunking and claim decomposition; MiniCheck's
# sentence-level scoring is robust to approximate boundaries.
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[A-Z0-9])")
_WS = re.compile(r"\s+")


def split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    out: list[str] = []
    for block in text.split("\n"):
        block = block.strip()
        if not block:
            continue
        out.extend(s.strip() for s in _SENT_END.split(block) if s.strip())
    return out


class MiniCheck:
    """Sentence-level fact-checker. Thread-unsafe (single model); construct once
    and reuse across a run."""

    def __init__(
        self,
        model: Optional[str] = None,
        device: Optional[str] = None,
        batch_size: int = 16,
        chunk_words: int = 500,
        max_input_length: int = 2048,
    ) -> None:
        # Local path first (config.MINICHECK_PATH), else the HF id.
        import os
        model = model or (
            config.MINICHECK_PATH if os.path.exists(config.MINICHECK_PATH)
            else config.MINICHECK_HF_ID
        )
        self.model, self.tokenizer, self.device = clients.load_entailment(
            model, device=device)
        self.batch_size = batch_size
        self.chunk_words = chunk_words
        self.max_input_length = max_input_length

    # -- document chunking (word-budgeted, sentence-aligned) ------------------
    def _doc_chunks(self, doc: str) -> list[str]:
        sents = split_sentences(doc) or [doc or ""]
        chunks: list[str] = []
        cur: list[str] = []
        n = 0
        for s in sents:
            w = len(s.split())
            if cur and n + w > self.chunk_words:
                chunks.append(" ".join(cur))
                cur, n = [s], w
            else:
                cur.append(s)
                n += w
        if cur:
            chunks.append(" ".join(cur))
        return [c for c in (x.strip() for x in chunks) if c] or [""]

    # -- raw batched support prob for (doc_chunk, claim) pairs ----------------
    def _support_probs(self, docs: list[str], claims: list[str]) -> list[float]:
        import torch

        eos = self.tokenizer.eos_token or "</s>"
        texts = ["predict: " + d + eos + c for d, c in zip(docs, claims)]
        probs: list[float] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            enc = self.tokenizer(
                batch, max_length=self.max_input_length, truncation=True,
                padding=True, return_tensors="pt",
            ).to(self.device)
            with torch.no_grad():
                dec = torch.zeros(
                    (enc["input_ids"].size(0), 1), dtype=torch.long,
                ).to(self.device)
                logits = self.model(
                    input_ids=enc["input_ids"],
                    attention_mask=enc["attention_mask"],
                    decoder_input_ids=dec,
                ).logits.squeeze(1)
                label_logits = logits[:, [_NO_SUPPORT_ID, _SUPPORT_ID]]
                p = torch.softmax(label_logits.float(), dim=-1)[:, 1]
            probs.extend(p.cpu().tolist())
        return probs

    # -- public: support prob for one (document, claim) -----------------------
    def score_claim(self, document: str, claim: str) -> float:
        """Max support prob of ``claim`` over ``document``'s chunks, in [0, 1]."""
        chunks = self._doc_chunks(document)
        return max(self._support_probs(chunks, [claim] * len(chunks)))

    def score_claims(self, document: str, claims: list[str]) -> list[float]:
        """Support prob for each claim vs the document (one chunking, batched)."""
        chunks = self._doc_chunks(document)
        docs: list[str] = []
        cl: list[str] = []
        for c in claims:
            docs.extend(chunks)
            cl.extend([c] * len(chunks))
        flat = self._support_probs(docs, cl) if claims else []
        out: list[float] = []
        k = len(chunks)
        for j in range(len(claims)):
            out.append(max(flat[j * k:(j + 1) * k]))
        return out

    # -- public: answer-level faithfulness ------------------------------------
    def score_answer(
        self,
        answer: str,
        contexts: list[str],
        threshold: float = 0.5,
    ) -> dict:
        """Split ``answer`` into claims (sentences) and score each against the
        concatenated ``contexts``. Returns the Track B answer-level metrics."""
        document = "\n\n".join(c for c in (contexts or []) if c and c.strip())
        claims = split_sentences(answer)
        if not claims:
            return {
                "n_claims": 0, "mean_support": None, "min_support": None,
                "n_supported": 0, "frac_supported": None,
                "fully_supported": None, "any_unsupported": None, "claims": [],
            }
        probs = self.score_claims(document, claims)
        supported = [p >= threshold for p in probs]
        return {
            "n_claims": len(claims),
            "mean_support": sum(probs) / len(probs),
            "min_support": min(probs),
            "n_supported": sum(supported),
            "frac_supported": sum(supported) / len(supported),
            "fully_supported": all(supported),
            "any_unsupported": not all(supported),
            "claims": [
                {"claim": c, "support": p, "supported": s}
                for c, p, s in zip(claims, probs, supported)
            ],
        }
