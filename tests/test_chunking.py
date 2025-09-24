from __future__ import annotations

from src.context.chunking import ChunkPiece, ContextChunker


def test_chunk_document_splits_by_length():
    chunker = ContextChunker(max_chars=20, overlap=5)
    text = "Lorem ipsum dolor sit amet, consectetur adipiscing elit."
    chunks = chunker._chunk_document(text)
    assert len(chunks) > 1
    assert isinstance(chunks[0], ChunkPiece)
    assert sum(len(piece.text) for piece in chunks) >= len(text) - 5


def test_chunk_python_ast_produces_function_blocks(sample_python_file):
    chunker = ContextChunker(max_chars=120, overlap=0)
    pieces = chunker._chunk_python_ast(sample_python_file)
    labels = [piece.label for piece in pieces]
    assert "class Greeter" in labels
    assert any(label.startswith("function") for label in labels if label)


def test_chunk_generic_code_identifies_exports(sample_js_file):
    chunker = ContextChunker(max_chars=200, overlap=0)
    pieces = chunker._chunk_generic_code(sample_js_file)
    assert any("export function" in (piece.label or "") for piece in pieces)
    assert any("export class" in (piece.label or "") for piece in pieces)


def test_interesting_path_detection():
    chunker = ContextChunker(max_chars=100, overlap=0)
    assert chunker.is_interesting_path("docs/guide.md")
    assert chunker.is_document_path("README.md")
    assert chunker.is_code_path("src/app.py")
    assert not chunker.is_interesting_path("assets/logo.svg")
