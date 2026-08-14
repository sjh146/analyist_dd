"""
Article normalization.

Pure functions (no DB/network dependency) that clean raw article text and
dates into a canonical form suitable for downstream analysis and dedup.

Contract
--------
``normalize_article(article) -> Optional[Article]``
    Returns a new :class:`Article` with normalized ``title``, ``content``,
    ``published_at`` (KST-aware datetime) and ``source``/``url`` preserved.
    Returns ``None`` when the article is invalid and must be discarded:
    empty/``None`` content, or content shorter than ``MIN_CONTENT_LENGTH``
    (30 chars) after normalization.
"""

import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional

from bs4 import BeautifulSoup

from app.models.schemas import Article

# Minimum length (in characters) of normalized content for an article to be
# considered valid. Shorter content is discarded.
MIN_CONTENT_LENGTH = 30

# Korea Standard Time (UTC+9).
KST = timezone(timedelta(hours=9))

# Collapse runs of whitespace (incl. newlines/tabs) into a single space.
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """Remove HTML tags and unescape entities from ``text``."""
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text(separator=" ")


def normalize_text(text: str) -> str:
    """Normalize text: strip HTML, NFKC, collapse whitespace, strip edges."""
    if not text:
        return ""
    text = strip_html(text)
    # NFKC: full-width -> half-width, normalize special chars/newlines/spaces.
    text = unicodedata.normalize("NFKC", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def normalize_datetime(value: Optional[datetime]) -> Optional[datetime]:
    """Convert a timezone-aware UTC datetime to KST; leave naive datetimes as-is."""
    if value is None:
        return None
    if value.tzinfo is not None:
        # Convert to KST regardless of the source timezone.
        return value.astimezone(KST)
    # Naive datetime: keep as-is (no timezone information to convert).
    return value


def normalize_article(article: Article) -> Optional[Article]:
    """Return a normalized copy of ``article``, or ``None`` if invalid.

    Normalization:
      - ``title``: HTML stripped, NFKC, whitespace collapsed.
      - ``content``: HTML stripped, NFKC, whitespace collapsed.
      - ``published_at``: timezone-aware UTC -> KST; naive kept as-is.
      - ``source`` / ``url``: preserved unchanged.

    Invalid (discarded) when normalized content is empty/``None`` or shorter
    than ``MIN_CONTENT_LENGTH`` characters.
    """
    if article is None:
        return None

    title = normalize_text(article.title or "")
    content = normalize_text(article.content or "")

    if len(content) < MIN_CONTENT_LENGTH:
        return None

    return Article(
        source=article.source,
        title=title,
        content=content,
        url=article.url,
        published_at=normalize_datetime(article.published_at),
    )
