from __future__ import annotations

import json
import logging
from typing import Dict, List, Sequence

from openai import OpenAI

from .context_store import VectorDocument

logger = logging.getLogger(__name__)


RERANKER_SYSTEM_PROMPT = """You are an expert context selection assistant helping with AI code reviews.
Rank the provided repository context chunks by how useful they are for understanding or reviewing the given PR diff.
Always return strict JSON with a `ranking` array sorted in descending usefulness. Each element must include:
- id: the chunk identifier
- score: integer 1-5 where 5 is most relevant
Limit the list to the chunks you would keep (max requested top_k)."""


class OpenAIReranker:
    def __init__(self, model: str, max_chars: int = 900):
        self.client = OpenAI()
        self.model = model
        self.max_chars = max_chars

    def rerank(self, query: str, documents: Sequence[VectorDocument], top_k: int) -> List[VectorDocument]:
        if not documents:
            return []
        if len(documents) <= top_k:
            return list(documents)

        payload = []
        for idx, doc in enumerate(documents, 1):
            snippet = doc.content[: self.max_chars]
            chunk_header = {
                "id": doc.id,
                "path": doc.file_path,
                "start_line": doc.start_line,
                "end_line": doc.end_line,
                "label": doc.label,
                "snippet": snippet,
            }
            payload.append(chunk_header)

        user_instructions = {
            "query": query,
            "top_k": top_k,
            "candidates": payload,
        }

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                temperature=0.0,
                messages=[
                    {"role": "system", "content": RERANKER_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(user_instructions)},
                ],
            )
            content = response.choices[0].message.content
            data = json.loads(content)
            ranking = data.get("ranking", [])
        except Exception as exc:
            logger.warning("Reranker failed (%s); falling back to embedding order", exc)
            return list(documents)[:top_k]

        order_map: Dict[str, VectorDocument] = {doc.id: doc for doc in documents}
        scored_docs: List[tuple[float, VectorDocument]] = []
        seen_ids = set()
        for entry in ranking:
            doc_id = entry.get("id")
            if not doc_id or doc_id in seen_ids:
                continue
            doc = order_map.get(doc_id)
            if not doc:
                continue
            try:
                score = float(entry.get("score", 0))
            except (TypeError, ValueError):
                score = 0.0
            scored_docs.append((score, doc))
            seen_ids.add(doc_id)

        # Fill remaining slots with original order while preserving stability
        for doc in documents:
            if doc.id in seen_ids:
                continue
            scored_docs.append((0.0, doc))

        scored_docs.sort(key=lambda item: item[0], reverse=True)
        return [doc for _, doc in scored_docs[:top_k]]
