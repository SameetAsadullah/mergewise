from __future__ import annotations

import json
import logging
from typing import List, Protocol, Sequence

from openai import OpenAI

from .store import VectorDocument

logger = logging.getLogger(__name__)


class ContextReranker(Protocol):
    def rerank(self, query: str, documents: Sequence[VectorDocument], top_k: int) -> List[VectorDocument]:
        ...


_RERANKER_SYSTEM_PROMPT = """
You are an expert context selection assistant helping with AI code reviews.
Rank the provided repository context chunks by how useful they are for understanding or reviewing the given PR diff.
Always return strict JSON with a `ranking` array sorted in descending usefulness.
Each element must include { id: string, score: integer 1-5 }. Limit the list to the chunks you would keep.
""".strip()


class OpenAIReranker:
    """LLM-powered reranker that refines embedding-based retrieval order."""

    def __init__(self, client: OpenAI, model: str, max_chars: int = 900) -> None:
        self._client = client
        self._model = model
        self._max_chars = max_chars

    def rerank(self, query: str, documents: Sequence[VectorDocument], top_k: int) -> List[VectorDocument]:
        if not documents or len(documents) <= top_k:
            return list(documents)

        payload = []
        for doc in documents:
            payload.append(
                {
                    "id": doc.id,
                    "path": doc.file_path,
                    "start_line": doc.start_line,
                    "end_line": doc.end_line,
                    "label": doc.label,
                    "snippet": doc.content[: self._max_chars],
                }
            )

        instructions = {
            "query": query,
            "top_k": top_k,
            "candidates": payload,
        }

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                temperature=0.0,
                messages=[
                    {"role": "system", "content": _RERANKER_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(instructions)},
                ],
            )
            content = response.choices[0].message.content
            ranking_payload = json.loads(content).get("ranking", [])
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("Reranker failed (%s); falling back to embedding order", exc)
            return list(documents)[:top_k]

        scored = []
        seen_ids = set()
        doc_map = {doc.id: doc for doc in documents}
        for item in ranking_payload:
            doc_id = item.get("id")
            if not doc_id or doc_id in seen_ids:
                continue
            doc = doc_map.get(doc_id)
            if not doc:
                continue
            try:
                score = float(item.get("score", 0))
            except (TypeError, ValueError):
                score = 0.0
            scored.append((score, doc))
            seen_ids.add(doc_id)

        for doc in documents:
            if doc.id not in seen_ids:
                scored.append((0.0, doc))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]
