"""search_product: find a product and its location in the kiosk's own store."""

from app.models.tools import ProductInfo, ProductSearchResult, ToolAction
from app.tools.context import ToolContext, ToolInputError

MAX_RESULTS = 5
MAX_QUERY_LENGTH = 100


async def search_product(ctx: ToolContext, query: str) -> ProductSearchResult:
    query = query.strip()[:MAX_QUERY_LENGTH]
    if not query:
        raise ToolInputError("query must not be empty")

    matches = await ctx.repository.search_products(ctx.kiosk.store_id, query, limit=MAX_RESULTS)
    if matches.products:
        return ProductSearchResult(
            action=ToolAction.PRODUCT_LOCATION,
            found=True,
            total_matches=matches.total,
            products=[ProductInfo.from_product(p) for p in matches.products],
        )
    return ProductSearchResult(
        action=ToolAction.PRODUCT_NOT_FOUND,
        found=False,
        total_matches=0,
        suggestions=[ProductInfo.from_product(p) for p in matches.suggestions],
    )
