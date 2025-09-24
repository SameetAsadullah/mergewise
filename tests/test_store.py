from __future__ import annotations

from src.context.store import FaissVectorStore, VectorDocument


def _make_doc(idx: int, dim: int = 4) -> VectorDocument:
    embedding = [float(idx + i) for i in range(dim)]
    return VectorDocument(
        id=f"doc-{idx}",
        file_path=f"file{idx}.txt",
        content=f"content-{idx}",
        start_line=idx,
        end_line=idx + 1,
        label="module",
        embedding=embedding,
    )


def test_replace_all_and_similarity(tmp_path):
    store = FaissVectorStore(tmp_path)
    docs = [_make_doc(i) for i in range(3)]
    store.replace_all(docs, metadata={"commit_sha": "abc"})

    assert store.metadata["commit_sha"] == "abc"
    assert len(store.documents) == 3

    query = [float(2 + i) for i in range(4)]
    results = store.similarity_search(query, top_k=2)
    assert [doc.id for doc in results] == ["doc-2", "doc-1"]


def test_add_documents_and_persistence(tmp_path):
    store = FaissVectorStore(tmp_path)
    store.replace_all([_make_doc(0)])
    store.add_documents([_make_doc(1)])

    assert len(store.documents) == 2

    # Reload from disk
    store2 = FaissVectorStore(tmp_path)
    store2.load()
    assert len(store2.documents) == 2
    assert {doc.id for doc in store2.documents} == {"doc-0", "doc-1"}
