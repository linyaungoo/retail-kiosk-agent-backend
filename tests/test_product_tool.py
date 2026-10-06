import time

import pytest

from app.models.business import Availability
from app.models.tools import ToolAction
from app.tools.context import ToolInputError
from app.tools.product_tool import MAX_RESULTS, search_product
from tests.conftest import MakeContext


async def _ids(make_ctx: MakeContext, query: str, store_id: str = "STORE-001") -> list[str]:
    result = await search_product(make_ctx(store_id=store_id), query)
    return [p.product_id for p in result.products]


async def test_coca_cola_returns_all_active_variants(make_ctx: MakeContext) -> None:
    result = await search_product(make_ctx(), "Coca Cola")
    assert result.found and result.action is ToolAction.PRODUCT_LOCATION
    assert {p.product_id for p in result.products} == {"P001", "P002", "P007", "P008"}
    assert result.total_matches == 4  # P032 (Vanilla) is inactive


async def test_exact_name_is_ranked_first_with_location(make_ctx: MakeContext) -> None:
    result = await search_product(make_ctx(), "Coca Cola 1L")
    best = result.products[0]
    assert (best.product_id, best.aisle, best.rack, best.shelf) == ("P001", "A03", "R02", "S02")
    assert best.product_name == "Coca Cola 1L"


@pytest.mark.parametrize("query", ["Coca", "coca cola", "COCA COLA"])
async def test_partial_and_case_insensitive(make_ctx: MakeContext, query: str) -> None:
    assert "P001" in await _ids(make_ctx, query)


@pytest.mark.parametrize("query", ["CC-1L", "cc-1l", "cc 1l", "100001"])
async def test_sku_and_barcode(make_ctx: MakeContext, query: str) -> None:
    assert await _ids(make_ctx, query) == ["P001"]


@pytest.mark.parametrize(
    "query", ["Head & Shoulders", "head and shoulders", "Head & Shoulders shampoo"]
)
async def test_head_and_shoulders(make_ctx: MakeContext, query: str) -> None:
    assert set(await _ids(make_ctx, query)) == {"P003", "P015"}


async def test_colgate_toothpaste_excludes_toothbrush(make_ctx: MakeContext) -> None:
    assert set(await _ids(make_ctx, "Colgate toothpaste")) == {"P004", "P018"}


async def test_brand_returns_all_brand_products(make_ctx: MakeContext) -> None:
    assert set(await _ids(make_ctx, "Colgate")) == {"P004", "P018", "P019"}
    assert set(await _ids(make_ctx, "Dettol")) == {"P022", "P024"}


async def test_product_type_matches_across_brands(make_ctx: MakeContext) -> None:
    assert set(await _ids(make_ctx, "shampoo")) == {"P003", "P015", "P016", "P017"}


async def test_category_search_is_capped(make_ctx: MakeContext) -> None:
    result = await search_product(make_ctx(), "beverages")
    assert result.total_matches > MAX_RESULTS
    assert len(result.products) == MAX_RESULTS


async def test_out_of_stock_is_found_not_hidden(make_ctx: MakeContext) -> None:
    result = await search_product(make_ctx(), "Pantene")
    assert result.found
    assert result.products[0].availability is Availability.OUT_OF_STOCK


async def test_inactive_product_not_found_but_similar_suggested(make_ctx: MakeContext) -> None:
    result = await search_product(make_ctx(), "Coca Cola Vanilla")
    assert not result.found and result.action is ToolAction.PRODUCT_NOT_FOUND
    assert result.products == []
    suggested = {p.product_id for p in result.suggestions}
    assert suggested and "P032" not in suggested


async def test_unknown_product(make_ctx: MakeContext) -> None:
    result = await search_product(make_ctx(), "iPhone")
    assert not result.found and result.action is ToolAction.PRODUCT_NOT_FOUND
    assert result.suggestions == []


async def test_store_isolation_product_only_in_other_store(make_ctx: MakeContext) -> None:
    assert (await search_product(make_ctx(store_id="STORE-001"), "ice cream")).found is False
    assert await _ids(make_ctx, "ice cream", store_id="STORE-002") == ["P033"]


async def test_store_isolation_location_and_stock_per_store(make_ctx: MakeContext) -> None:
    store2 = make_ctx(store_id="STORE-002")
    coke = (await search_product(store2, "Coca Cola 1L")).products[0]
    assert (coke.aisle, coke.rack, coke.shelf) == ("B02", "R01", "S03")
    hs = (await search_product(store2, "Head & Shoulders 400ml")).products[0]
    assert hs.availability is Availability.OUT_OF_STOCK


async def test_unknown_store_finds_nothing(make_ctx: MakeContext) -> None:
    assert (await search_product(make_ctx(store_id="STORE-999"), "Coca Cola")).found is False


@pytest.mark.parametrize("query", ["", "   "])
async def test_empty_query_rejected(make_ctx: MakeContext, query: str) -> None:
    with pytest.raises(ToolInputError):
        await search_product(make_ctx(), query)


async def test_cached_search_latency(make_ctx: MakeContext) -> None:
    ctx = make_ctx()
    queries = ["Coca Cola", "Head & Shoulders shampoo", "Colgate toothpaste", "iPhone", "100001"]
    runs = 200
    start = time.perf_counter()
    for _ in range(runs):
        for q in queries:
            await search_product(ctx, q)
    avg_ms = (time.perf_counter() - start) * 1000 / (runs * len(queries))
    assert avg_ms < 5, f"average search took {avg_ms:.3f} ms"
