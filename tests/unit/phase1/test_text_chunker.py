"""TextChunker unit tests."""

from __future__ import annotations

from litemiro.phase1.text_chunker import TextChunker


class TestTextChunker:
    def test_short_text_single_chunk(self) -> None:
        chunker = TextChunker(chunk_size=1000, overlap=100)
        chunks = chunker.chunk("짧은 텍스트")
        assert len(chunks) == 1
        assert chunks[0].text == "짧은 텍스트"
        assert chunks[0].index == 0

    def test_empty_text(self) -> None:
        chunker = TextChunker()
        chunks = chunker.chunk("")
        assert len(chunks) == 0

    def test_whitespace_only(self) -> None:
        chunker = TextChunker()
        chunks = chunker.chunk("   \n\n  ")
        assert len(chunks) == 0

    def test_multiple_chunks_with_overlap(self) -> None:
        text = "A" * 500 + ". " + "B" * 500 + ". " + "C" * 500
        chunker = TextChunker(chunk_size=600, overlap=50)
        chunks = chunker.chunk(text)
        assert len(chunks) >= 2
        for i, chunk in enumerate(chunks):
            assert chunk.index == i
            assert len(chunk.text) <= 600 + 50

    def test_batch_grouping(self) -> None:
        chunker = TextChunker(chunk_size=100, overlap=10)
        text = "문장입니다. " * 100
        chunks = chunker.chunk(text)
        batches = chunker.batch(chunks, batch_size=5)
        for batch in batches[:-1]:
            assert len(batch) == 5
        assert len(batches[-1]) <= 5

    def test_batch_empty(self) -> None:
        chunker = TextChunker()
        batches = chunker.batch([], batch_size=5)
        assert batches == []

    def test_chunk_boundaries(self) -> None:
        chunker = TextChunker(chunk_size=1000, overlap=100)
        text = "첫 번째 문단입니다.\n\n두 번째 문단입니다.\n\n세 번째 문단입니다."
        chunks = chunker.chunk(text)
        assert len(chunks) >= 1
        assert chunks[0].start_char == 0
