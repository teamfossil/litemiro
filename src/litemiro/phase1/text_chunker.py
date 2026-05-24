from __future__ import annotations

from litemiro.phase1.models import TextChunk


class TextChunker:
    def __init__(self, chunk_size: int = 1000, overlap: int = 100) -> None:
        self._chunk_size = chunk_size
        self._overlap = overlap

    def chunk(self, text: str) -> list[TextChunk]:
        if not text.strip():
            return []
        if len(text) <= self._chunk_size:
            return [TextChunk(index=0, text=text, start_char=0, end_char=len(text))]

        chunks: list[TextChunk] = []
        start = 0
        index = 0

        while start < len(text):
            end = min(start + self._chunk_size, len(text))

            if end < len(text):
                # try to split on sentence boundary within the last 200 chars
                search_start = max(start, end - 200)
                best_break = -1
                for sep in ("\n\n", "\n", ". ", "? ", "! "):
                    pos = text.rfind(sep, search_start, end)
                    if pos != -1 and pos > best_break:
                        best_break = pos + len(sep)
                if best_break > start:
                    end = best_break

            chunks.append(
                TextChunk(index=index, text=text[start:end], start_char=start, end_char=end)
            )
            index += 1
            next_start = end - self._overlap
            start = next_start if next_start > start else end

        return chunks

    def batch(self, chunks: list[TextChunk], batch_size: int = 5) -> list[list[TextChunk]]:
        return [chunks[i : i + batch_size] for i in range(0, len(chunks), batch_size)]
