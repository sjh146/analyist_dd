"""
Content-based duplicate detection for normalized articles.

Contract
--------
``dedupe(articles) -> (unique, dropped)``
    Given a list of already-normalized :class:`Article` objects, returns a
    tuple ``(unique, dropped)`` where ``unique`` is the deduplicated list
    (first occurrence of each duplicate group kept) and ``dropped`` is the
    list of articles removed as duplicates.

    Two articles are considered duplicates when either:
      - their normalized title hashes are equal, or
      - the Jaccard similarity of their body token sets is >= ``JACCARD_THRESHOLD``.

    This keeps one copy of the same story across different media outlets.
"""

import hashlib
import re
from typing import List, Tuple

from app.models.schemas import Article

# Jaccard similarity threshold above which two bodies are considered duplicates.
JACCARD_THRESHOLD = 0.6

# Tokenize on non-alphanumeric boundaries (Korean/ASCII/CJK aware).
_TOKEN_RE = re.compile(r"[^\w]+", re.UNICODE)


def _title_hash(article: Article) -> str:
    """Stable hash of the normalized title."""
    title = (article.title or "").strip()
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


def _tokens(text: str) -> set:
    """Tokenize text into a set of lowercase tokens."""
    if not text:
        return set()
    return {t.lower() for t in _TOKEN_RE.split(text) if t}


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _is_duplicate(a: Article, b: Article) -> bool:
    """Return True if ``a`` and ``b`` are considered the same story."""
    if _title_hash(a) == _title_hash(b):
        return True
    return _jaccard(_tokens(a.content or ""), _tokens(b.content or "")) >= JACCARD_THRESHOLD


def dedupe(articles: List[Article]) -> Tuple[List[Article], List[Article]]:
    """Deduplicate a list of normalized articles.

    Keeps the first occurrence of each duplicate group; returns the kept
    articles and the dropped ones.
    """
    unique: List[Article] = []
    dropped: List[Article] = []

    for article in articles:
        if any(_is_duplicate(article, kept) for kept in unique):
            dropped.append(article)
        else:
            unique.append(article)

    return unique, dropped
