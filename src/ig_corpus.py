"""Persist IG discovery corpus + embedding cache for semantic dedup."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR, settings
from ig_store import IgStore, normalize_handle

CORPUS_PATH = DATA_DIR / "ig_corpus.jsonl"
EMBEDDINGS_PATH = DATA_DIR / "ig_embeddings.npy"
INDEX_PATH = DATA_DIR / "ig_embeddings_index.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class IgCorpus:
    """Append-only profile corpus with cached CLIP embeddings."""

    def __init__(self) -> None:
        self.corpus_path = CORPUS_PATH
        self.embeddings_path = EMBEDDINGS_PATH
        self.index_path = INDEX_PATH
        self._embeddings: np.ndarray | None = None
        self._index: list[str] | None = None
        self._excluded_handles: set[str] | None = None

    def _load_index(self) -> tuple[np.ndarray, list[str]]:
        if self._embeddings is not None and self._index is not None:
            return self._embeddings, self._index
        if self.embeddings_path.exists() and self.index_path.exists():
            try:
                self._embeddings = np.load(self.embeddings_path)
                self._index = json.loads(self.index_path.read_text())
                if len(self._index) != len(self._embeddings):
                    self._embeddings = np.zeros((0, 512), dtype=np.float32)
                    self._index = []
            except (OSError, json.JSONDecodeError, ValueError):
                self._embeddings = np.zeros((0, 512), dtype=np.float32)
                self._index = []
        else:
            self._embeddings = np.zeros((0, 512), dtype=np.float32)
            self._index = []
        return self._embeddings, self._index

    def _save_index(self, embeddings: np.ndarray, index: list[str]) -> None:
        np.save(self.embeddings_path, embeddings)
        self.index_path.write_text(json.dumps(index))
        self._embeddings = embeddings
        self._index = index

    def exclusion_handles(self, store: IgStore | None = None) -> set[str]:
        """Handles to treat as semantic-exclusion anchors."""
        if self._excluded_handles is not None:
            return self._excluded_handles

        store = store or IgStore()
        excluded: set[str] = set(store._dnc_handles())
        for row in store.all():
            handle = normalize_handle(row.get("ig_handle", ""))
            if not handle:
                continue
            status = (row.get("status") or "").lower()
            if status in {"sent", "dnc", "skipped"}:
                excluded.add(handle)

        if self.corpus_path.exists():
            try:
                for line in self.corpus_path.read_text().splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    handle = normalize_handle(rec.get("ig_handle", ""))
                    if not handle:
                        continue
                    if rec.get("already_following") or rec.get("queued") is False:
                        if rec.get("exclude_anchor"):
                            excluded.add(handle)
                    if rec.get("status") in {"sent", "dnc", "skipped"}:
                        excluded.add(handle)
            except (OSError, json.JSONDecodeError):
                pass

        self._excluded_handles = excluded
        return excluded

    def exclusion_embeddings(self, store: IgStore | None = None) -> tuple[np.ndarray, list[str]]:
        """Embeddings for excluded/sent/following profiles."""
        excluded = self.exclusion_handles(store)
        embeddings, index = self._load_index()
        if not len(index):
            return np.zeros((0, embeddings.shape[1] if embeddings.ndim == 2 else 512), dtype=np.float32), []

        mask_handles: list[str] = []
        mask_rows: list[np.ndarray] = []
        for i, handle in enumerate(index):
            if handle in excluded:
                mask_handles.append(handle)
                mask_rows.append(embeddings[i])
        if not mask_rows:
            return np.zeros((0, embeddings.shape[1]), dtype=np.float32), []
        return np.stack(mask_rows), mask_handles

    def is_semantic_duplicate(
        self,
        embedding: np.ndarray,
        store: IgStore | None = None,
        threshold: float | None = None,
    ) -> tuple[bool, str]:
        """True if ``embedding`` is too similar to an excluded profile."""
        threshold = threshold if threshold is not None else settings.ig_semantic_dup_threshold
        ex_emb, ex_handles = self.exclusion_embeddings(store)
        if ex_emb.shape[0] == 0:
            return False, ""

        emb = embedding.astype(np.float32)
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm
        sims = ex_emb @ emb
        best_idx = int(np.argmax(sims))
        best_sim = float(sims[best_idx])
        if best_sim >= threshold:
            return True, ex_handles[best_idx]
        return False, ""

    def record(
        self,
        handle: str,
        profile_doc: str,
        scores: dict,
        reasons: list[str] | str,
        *,
        queued: bool = False,
        already_following: bool = False,
        status: str = "",
        exclude_anchor: bool = False,
    ) -> None:
        """Append a corpus row and update the embedding cache."""
        handle = normalize_handle(handle)
        if not handle:
            return

        embedding = scores.get("embedding")
        if embedding is None:
            return

        if isinstance(reasons, str):
            try:
                reasons = json.loads(reasons)
            except json.JSONDecodeError:
                reasons = [reasons]

        record = {
            "ig_handle": handle,
            "profile_doc": profile_doc,
            "match_score": scores.get("match_score"),
            "city_score": scores.get("city_score"),
            "exclude_score": scores.get("exclude_score"),
            "rank_reasons": reasons,
            "queued": queued,
            "already_following": already_following,
            "status": status,
            "exclude_anchor": exclude_anchor,
            "timestamp": _now(),
        }
        with self.corpus_path.open("a") as f:
            f.write(json.dumps(record) + "\n")

        embeddings, index = self._load_index()
        emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if handle in index:
            idx = index.index(handle)
            if embeddings.ndim == 2 and embeddings.shape[0] > idx:
                embeddings[idx] = emb
            else:
                embeddings = np.vstack([embeddings, emb]) if len(embeddings) else emb.reshape(1, -1)
                index.append(handle)
        else:
            if len(embeddings) == 0:
                embeddings = emb.reshape(1, -1)
            else:
                embeddings = np.vstack([embeddings, emb.reshape(1, -1)])
            index.append(handle)
        self._save_index(embeddings, index)


_corpus: IgCorpus | None = None


def get_ig_corpus() -> IgCorpus:
    global _corpus
    if _corpus is None:
        _corpus = IgCorpus()
    return _corpus
