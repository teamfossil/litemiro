"""Topic extraction — owned by **B**.

Phase 2 turns free-form ``CREATE_POST`` content into the
``Post.topics`` tuple ``FeedEngine`` indexes for candidacy. The actual
embedder is the same ``EmbedderLike`` Protocol used by ``FeedEngine``
so a single sentence-transformers instance covers both surfaces in W3.
"""

from litemiro.topics.extractor import TopicExtractor

__all__ = ["TopicExtractor"]
