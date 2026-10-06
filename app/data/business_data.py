"""Immutable, pre-indexed snapshot of all business data.

Built once per load/refresh, then shared read-only by every request.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.data.parsing import (
    RawRow,
    parse_faq,
    parse_kiosk,
    parse_product,
    parse_rows,
    parse_store,
)
from app.data.search import IndexedFaq, IndexedProduct
from app.models.business import KioskRecord, Store
from app.utils.logging import get_logger

logger = get_logger(__name__)

RawRows = Iterable[RawRow]

PRODUCTS_TABLE = "PRODUCTS"
STORES_TABLE = "STORES"
FAQ_TABLE = "FAQ"
KIOSKS_TABLE = "KIOSKS"  # optional: kiosk -> store registry


@dataclass(frozen=True, slots=True)
class BusinessData:
    products_by_store: Mapping[str, tuple[IndexedProduct, ...]]
    stores: Mapping[str, Store]  # active stores only
    faqs: tuple[IndexedFaq, ...]  # active entries only
    brands: tuple[str, ...] = ()  # distinct active brand names (STT vocabulary)
    kiosks: Mapping[str, KioskRecord] = field(default_factory=dict)  # active kiosks only

    @classmethod
    def from_rows(
        cls,
        products: RawRows,
        stores: RawRows,
        faqs: RawRows,
        kiosks: RawRows = (),
    ) -> "BusinessData":
        by_store: dict[str, list[IndexedProduct]] = defaultdict(list)
        seen_products: set[tuple[str, str]] = set()
        for product in parse_rows(products, parse_product, PRODUCTS_TABLE):
            key = (product.product_id, product.store_id)
            if key in seen_products:
                logger.warning(
                    "duplicate_row_skipped",
                    extra={"table": PRODUCTS_TABLE, "product_id": key[0], "store_id": key[1]},
                )
                continue
            seen_products.add(key)
            if product.active:
                by_store[product.store_id].append(IndexedProduct.build(product))

        store_map: dict[str, Store] = {}
        for store in parse_rows(stores, parse_store, STORES_TABLE):
            if store.store_id in store_map:
                logger.warning(
                    "duplicate_row_skipped",
                    extra={"table": STORES_TABLE, "store_id": store.store_id},
                )
                continue
            if store.active:
                store_map[store.store_id] = store

        faq_entries = tuple(
            IndexedFaq.build(faq) for faq in parse_rows(faqs, parse_faq, FAQ_TABLE) if faq.active
        )

        kiosk_map: dict[str, KioskRecord] = {}
        seen_kiosks: set[str] = set()
        for kiosk in parse_rows(kiosks, parse_kiosk, KIOSKS_TABLE):
            if kiosk.kiosk_id in seen_kiosks:
                logger.warning(
                    "duplicate_row_skipped",
                    extra={"table": KIOSKS_TABLE, "kiosk_id": kiosk.kiosk_id},
                )
                continue
            seen_kiosks.add(kiosk.kiosk_id)
            if kiosk.active:
                kiosk_map[kiosk.kiosk_id] = kiosk

        brands = {i.product.brand for items in by_store.values() for i in items if i.product.brand}
        return cls(
            products_by_store={store_id: tuple(items) for store_id, items in by_store.items()},
            stores=store_map,
            faqs=faq_entries,
            brands=tuple(sorted(brands, key=str.casefold)),
            kiosks=kiosk_map,
        )

    def counts(self) -> dict[str, int]:
        return {
            "products": sum(len(items) for items in self.products_by_store.values()),
            "stores": len(self.stores),
            "faqs": len(self.faqs),
            "kiosks": len(self.kiosks),
        }
