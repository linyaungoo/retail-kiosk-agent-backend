import pytest

from app.models.tools import ToolAction
from app.tools.context import ToolInputError
from app.tools.faq_tool import search_faq
from tests.conftest import MakeContext


async def _ids(make_ctx: MakeContext, query: str, **ctx_kwargs: str) -> list[str]:
    result = await search_faq(make_ctx(**ctx_kwargs), query)
    return [a.faq_id for a in result.answers]


async def test_return_policy(make_ctx: MakeContext) -> None:
    result = await search_faq(make_ctx(), "return policy")
    assert result.found and result.action is ToolAction.FAQ
    assert result.answers[0].faq_id == "FAQ001"
    assert "7 days" in result.answers[0].answer


async def test_store_faq_overrides_global_same_category(make_ctx: MakeContext) -> None:
    ids = await _ids(make_ctx, "What is your return policy?", store_id="STORE-002")
    assert ids[0] == "FAQ008"
    assert "FAQ001" not in ids


async def test_burmese_language_returns_burmese_answer(make_ctx: MakeContext) -> None:
    result = await search_faq(make_ctx(language="my-MM"), "return policy")
    assert result.answers[0].answer.startswith("ဝယ်ယူပြီး ၇ ရက်အတွင်း")


async def test_burmese_query(make_ctx: MakeContext) -> None:
    assert (await _ids(make_ctx, "ပြန်အပ်လို့ရလား", language="my-MM"))[0] == "FAQ001"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("How can I pay?", "FAQ002"),
        ("Do you accept KBZPay?", "FAQ002"),
        ("How do I become a member?", "FAQ003"),
        ("refund", "FAQ005"),
        ("loyalty points", "FAQ006"),
        ("plastic bag", "FAQ007"),
        ("wifi", "FAQ010"),
        ("exchange expired food", "FAQ012"),
    ],
)
async def test_global_faqs(make_ctx: MakeContext, query: str, expected: str) -> None:
    assert (await _ids(make_ctx, query))[0] == expected


async def test_store_only_faq(make_ctx: MakeContext) -> None:
    assert (await _ids(make_ctx, "home delivery", store_id="STORE-001"))[0] == "FAQ004"
    assert (await _ids(make_ctx, "home delivery", store_id="STORE-002"))[0] == "FAQ009"
    store3 = await search_faq(make_ctx(store_id="STORE-003", organization_id="ORG-002"), "delivery")
    assert not store3.found


async def test_inactive_faq_not_returned(make_ctx: MakeContext) -> None:
    assert "FAQ011" not in await _ids(make_ctx, "gift card")


async def test_keyword_overlap_can_return_related_but_wrong_faq(make_ctx: MakeContext) -> None:
    # Known limit of keyword search: "gift card" hits the payment FAQ via "card".
    # The agent must check that an FAQ actually answers the question before using it.
    assert await _ids(make_ctx, "gift card") == ["FAQ002"]


async def test_unrelated_query_not_found(make_ctx: MakeContext) -> None:
    result = await search_faq(make_ctx(), "weather tomorrow")
    assert not result.found and result.action is ToolAction.GENERAL


async def test_keyword_needs_whole_word(make_ctx: MakeContext) -> None:
    # "pay" must not match inside "repayment".
    assert "FAQ002" not in await _ids(make_ctx, "repayment")


async def test_empty_query_rejected(make_ctx: MakeContext) -> None:
    with pytest.raises(ToolInputError):
        await search_faq(make_ctx(), "  ")
