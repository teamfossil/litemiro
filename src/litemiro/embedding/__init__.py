"""Production ``EmbedderLike`` adapters.

Importing this module is cheap; the heavy ``sentence-transformers``
weights load on first ``embed`` call. Install with::

    pip install litemiro[embedding]
"""

from litemiro.embedding.sentence_transformers import STEmbedder

__all__ = ["STEmbedder"]
