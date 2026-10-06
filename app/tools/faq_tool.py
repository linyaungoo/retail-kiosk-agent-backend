"""search_faq: approved policy answers (returns, payment, membership, ...)."""

from app.models.business import FaqEntry
from app.models.kiosk import Language
from app.models.tools import FaqAnswer, FaqSearchResult, ToolAction
from app.tools.context import ToolContext, ToolInputError

MAX_ANSWERS = 2
MAX_QUERY_LENGTH = 200


def _answer_for(faq: FaqEntry, language: Language) -> str:
    if language == "my-MM":
        return faq.answer_mm or faq.answer_en
    return faq.answer_en or faq.answer_mm


async def search_faq(ctx: ToolContext, query: str) -> FaqSearchResult:
    query = query.strip()[:MAX_QUERY_LENGTH]
    if not query:
        raise ToolInputError("query must not be empty")

    faqs = await ctx.repository.search_faqs(ctx.kiosk.store_id, query, limit=MAX_ANSWERS)
    return FaqSearchResult(
        # No approved answer: nothing policy-specific for the kiosk UI to show.
        action=ToolAction.FAQ if faqs else ToolAction.GENERAL,
        found=bool(faqs),
        answers=[
            FaqAnswer(
                faq_id=faq.faq_id,
                category=faq.category,
                question=faq.question,
                answer=_answer_for(faq, ctx.kiosk.language),
            )
            for faq in faqs
        ],
    )
