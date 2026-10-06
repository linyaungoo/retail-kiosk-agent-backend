"""Deterministic in-memory search over cached business data.

The agent extracts the query; Python finds the records. No LLM touches the catalog.
These functions are the only part that would change for PostgreSQL full-text or
vector search; callers go through BusinessRepository.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.models.business import Availability, FaqEntry, FaqScope, Product

_APOSTROPHES = re.compile(r"['’`]")
# Sizes like "1.5l" / "330ml" stay one token; otherwise runs of letters or digits.
_TOKEN = re.compile(r"\d+(?:\.\d+)?[^\W\d_]*|[^\W\d_]+")
_NON_CODE = re.compile(r"[^0-9a-z]")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "the", "of", "for", "with", "in", "at", "on", "is", "are",
        "do", "does", "you", "your", "have", "has", "any", "what", "where", "which",
        "how", "i", "me", "my", "there", "to", "it", "this", "that", "please",
    }
)  # fmt: skip
_AVAILABILITY_RANK = {
    Availability.AVAILABLE: 0,
    Availability.LOW_STOCK: 1,
    Availability.OUT_OF_STOCK: 2,
}

MIN_PREFIX_LEN = 3
MIN_PARTIAL_COVERAGE = 0.5
MIN_FAQ_SCORE = 6


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("&", " and ")
    return " ".join(_APOSTROPHES.sub("", text).split())


def _stem(token: str) -> str:
    """Very light plural folding: shampoos -> shampoo, noodles -> noodle."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    tokens = (_stem(t) for t in _TOKEN.findall(normalize(text)) if t not in _STOPWORDS)
    return list(dict.fromkeys(tokens))  # dedupe, keep order


def code_key(text: str) -> str:
    """Comparable form of SKU/barcode: "CC-1L" == "cc 1l" == "cc1l"."""
    return _NON_CODE.sub("", normalize(text))


def _token_matches(query_token: str, tokens: Iterable[str]) -> bool:
    if len(query_token) < MIN_PREFIX_LEN:
        return query_token in tokens
    return any(t == query_token or t.startswith(query_token) for t in tokens)


# --- products ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndexedProduct:
    """A product plus search keys precomputed once per cache refresh."""

    product: Product
    codes: frozenset[str]
    name_key: str
    brand_key: str
    primary_tokens: tuple[str, ...]  # name + brand
    category_tokens: tuple[str, ...]  # category + zone
    name_token_count: int

    @classmethod
    def build(cls, product: Product) -> "IndexedProduct":
        name_tokens = tokenize(product.name)
        return cls(
            product=product,
            codes=frozenset(c for c in (code_key(product.sku), code_key(product.barcode)) if c),
            name_key=normalize(product.name),
            brand_key=normalize(product.brand),
            primary_tokens=tuple(dict.fromkeys([*name_tokens, *tokenize(product.brand)])),
            category_tokens=tuple(tokenize(f"{product.category} {product.zone}")),
            name_token_count=len(name_tokens),
        )


@dataclass(frozen=True, slots=True)
class ProductMatches:
    products: list[Product]
    total: int
    suggestions: list[Product]


def _rank_key(score: float, item: IndexedProduct) -> tuple[float, int, str]:
    return (-score, _AVAILABILITY_RANK[item.product.availability], item.product.name)


def search_products(
    items: Sequence[IndexedProduct],
    query: str,
    *,
    limit: int,
    suggestion_limit: int = 3,
) -> ProductMatches:
    """Match by SKU/barcode first, then by name/brand/category tokens.

    A product matches fully when every query token is found in its name, brand,
    category or zone (prefixes of 3+ characters count). If nothing matches fully,
    products covering at least half of the query are returned as suggestions.
    """
    key = code_key(query)
    if key:
        by_code = [i.product for i in items if key in i.codes]
        if by_code:
            return ProductMatches(by_code[:limit], len(by_code), [])

    query_key = normalize(query)
    query_tokens = tokenize(query)
    if not query_tokens:
        return ProductMatches([], 0, [])

    full: list[tuple[float, IndexedProduct]] = []
    partial: list[tuple[float, IndexedProduct]] = []
    for item in items:
        primary_hits = category_hits = 0
        for token in query_tokens:
            if _token_matches(token, item.primary_tokens):
                primary_hits += 1
            elif _token_matches(token, item.category_tokens):
                category_hits += 1
        hits = primary_hits + category_hits
        if hits == len(query_tokens):
            score = 500 + 100 * primary_hits / len(query_tokens)
            if item.name_key == query_key:
                score += 300
            elif item.brand_key == query_key:
                score += 50
            elif item.name_key.startswith(query_key):
                score += 30
            # Prefer the most specific product: fewer extra words in its name.
            score -= 2 * max(item.name_token_count - len(query_tokens), 0)
            full.append((score, item))
        elif hits / len(query_tokens) >= MIN_PARTIAL_COVERAGE:
            partial.append((hits / len(query_tokens), item))

    if full:
        full.sort(key=lambda pair: _rank_key(*pair))
        return ProductMatches([i.product for _, i in full[:limit]], len(full), [])
    partial.sort(key=lambda pair: _rank_key(*pair))
    return ProductMatches([], 0, [i.product for _, i in partial[:suggestion_limit]])


# --- FAQ --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndexedFaq:
    faq: FaqEntry
    keyword_patterns: tuple[tuple[re.Pattern[str], int], ...]
    question_tokens: frozenset[str]
    category_tokens: frozenset[str]

    @classmethod
    def build(cls, faq: FaqEntry) -> "IndexedFaq":
        patterns: list[tuple[re.Pattern[str], int]] = []
        for raw in faq.keywords:
            keyword = normalize(raw)
            if not keyword:
                continue
            if keyword.isascii():
                # Whole words only, so "pay" does not match "repay"; allow a plural "s".
                pattern = re.compile(rf"(?<!\w){re.escape(keyword)}s?(?!\w)")
            else:
                # Burmese has no spaces between words; substring match is the practical option.
                pattern = re.compile(re.escape(keyword))
            patterns.append((pattern, 15 if " " in keyword else 10))
        return cls(
            faq=faq,
            keyword_patterns=tuple(patterns),
            question_tokens=frozenset(t for t in tokenize(faq.question) if len(t) >= 3),
            category_tokens=frozenset(tokenize(faq.category.replace("_", " "))),
        )


def search_faqs(
    faqs: Sequence[IndexedFaq],
    store_id: str,
    query: str,
    *,
    limit: int,
) -> list[FaqEntry]:
    """Return approved FAQ entries for this store, best match first.

    A STORE-scoped entry replaces GLOBAL entries of the same category for that store
    (e.g. a branch with a shorter return window).
    """
    candidates = [f for f in faqs if f.faq.scope is FaqScope.GLOBAL or f.faq.store_id == store_id]
    store_categories = {f.faq.category for f in candidates if f.faq.scope is FaqScope.STORE}
    candidates = [
        f
        for f in candidates
        if f.faq.scope is FaqScope.STORE or f.faq.category not in store_categories
    ]

    query_key = normalize(query)
    query_tokens = set(tokenize(query))
    scored: list[tuple[int, FaqEntry]] = []
    for item in candidates:
        score = sum(w for pattern, w in item.keyword_patterns if pattern.search(query_key))
        score += 5 * len(item.category_tokens & query_tokens)
        score += 2 * len(item.question_tokens & query_tokens)
        if score >= MIN_FAQ_SCORE:
            scored.append((score, item.faq))

    if not scored:
        return []
    scored.sort(key=lambda pair: (-pair[0], pair[1].faq_id))
    best = scored[0][0]
    return [faq for score, faq in scored if score * 2 >= best][:limit]
