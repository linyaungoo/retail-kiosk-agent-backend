"""Template answers rendered from real tool results on the sample data."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.models.kiosk import Language
from app.models.tools import StoreTopic, ToolAction, ToolResult
from app.services.agent_service import finish_with_template
from app.services.agent_tools import AgentRunContext, ToolCallRecord
from app.services.answer_templates import mm_number, render_answer, spoken_time
from app.tools.bmi_tool import bmi_from_measurements
from app.tools.faq_tool import search_faq
from app.tools.product_tool import search_product
from app.tools.store_tool import get_store_info
from tests.conftest import MakeContext


def _record(name: str, result: ToolResult, **arguments: Any) -> ToolCallRecord:
    return ToolCallRecord(
        name=name,
        arguments=arguments,
        action=result.action,
        data=result.model_dump(mode="json", exclude_none=True, exclude={"action"}),
    )


async def _product(
    make_ctx: MakeContext,
    query: str,
    language: Language = "my-MM",
    store_id: str = "STORE-001",
    price_requested: bool = False,
) -> str | None:
    result = await search_product(make_ctx(store_id=store_id), query)
    record = _record("search_product", result, query=query, price_requested=price_requested)
    return render_answer(record, language)


# --- helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hhmm", "my", "en"),
    [
        ("21:00", "ည ၉ နာရီ", "9 PM"),
        ("08:00", "မနက် ၈ နာရီ", "8 AM"),
        ("08:30", "မနက် ၈ နာရီခွဲ", "8:30 AM"),
        ("12:00", "နေ့လည် ၁၂ နာရီ", "12 PM"),
        ("17:15", "ညနေ ၅ နာရီ ၁၅ မိနစ်", "5:15 PM"),
        ("00:00", "ည ၁၂ နာရီ", "12 AM"),
    ],
)
def test_spoken_time(hhmm: str, my: str, en: str) -> None:
    assert spoken_time(hhmm, "my-MM") == my
    assert spoken_time(hhmm, "en-US") == en


def test_burmese_digits() -> None:
    assert mm_number(2024) == "၂၀၂၄"


# --- products --------------------------------------------------------------------------


async def test_single_product(make_ctx: MakeContext) -> None:
    assert await _product(make_ctx, "Coca Cola 1L") == (
        "Coca Cola 1L ကို Aisle A03၊ Rack R02၊ Shelf S02 မှာ ရှာနိုင်ပါတယ်။"
    )
    assert await _product(make_ctx, "Coca Cola 1L", "en-US") == (
        "Coca Cola 1L is in aisle A03, rack R02, shelf S02."
    )


async def test_single_product_with_price(make_ctx: MakeContext) -> None:
    answer = await _product(make_ctx, "Coca Cola 1L", "en-US", price_requested=True)
    assert answer == "Coca Cola 1L is in aisle A03, rack R02, shelf S02. It costs 1,800 kyats."
    assert (await _product(make_ctx, "Coca Cola 1L", price_requested=True) or "").endswith(
        "ဈေးနှုန်းက 1,800 ကျပ် ပါ။"
    )


async def test_out_of_stock_single(make_ctx: MakeContext) -> None:
    assert (await _product(make_ctx, "Pantene", "en-US") or "").endswith(
        "It is currently out of stock."
    )
    assert (await _product(make_ctx, "Pantene") or "").endswith("ပစ္စည်းပြတ်နေပါတယ်။")


async def test_same_aisle_different_racks(make_ctx: MakeContext) -> None:
    # 4 Coca Cola products, all aisle A03 (racks R02 and R03); Zero is low stock.
    assert await _product(make_ctx, "Coca Cola") == (
        "Coca Cola ၄ မျိုးကို Aisle A03 မှာ ရှာနိုင်ပါတယ်။ Coca Cola Zero Can 330ml ကတော့ နည်းနည်းပဲ ကျန်ပါတော့တယ်။"
    )
    assert await _product(make_ctx, "Coca Cola", "en-US") == (
        "We have 4 Coca Cola products in aisle A03. Coca Cola Zero Can 330ml is running low."
    )


async def test_same_aisle_and_rack(make_ctx: MakeContext) -> None:
    assert await _product(make_ctx, "Head & Shoulders", "en-US") == (
        "We have 2 Head & Shoulders products in aisle A07, rack R01."
    )


async def test_different_aisles(make_ctx: MakeContext) -> None:
    # Dettol: soap (A07) and antiseptic liquid (A09).
    answer = await _product(make_ctx, "Dettol", "en-US") or ""
    assert "aisle A07" in answer and "aisle A09" in answer and "more options" not in answer


async def test_several_prices_left_to_llm(make_ctx: MakeContext) -> None:
    assert await _product(make_ctx, "Coca Cola", price_requested=True) is None


async def test_not_found_with_and_without_suggestions(make_ctx: MakeContext) -> None:
    assert await _product(make_ctx, "iPhone", "en-US") == (
        "Sorry, I couldn't find iPhone in this store. Please ask at the customer service counter."
    )
    vanilla = await _product(make_ctx, "Coca Cola Vanilla") or ""
    assert vanilla.startswith("တောင်းပန်ပါတယ်၊ Coca Cola Vanilla ကို ဒီဆိုင်မှာ မတွေ့ပါဘူး။")
    assert "ကိုတော့ Aisle A03" in vanilla


async def test_store_isolation_in_answer(make_ctx: MakeContext) -> None:
    answer = await _product(make_ctx, "Coca Cola 1L", "en-US", store_id="STORE-002")
    assert answer == "Coca Cola 1L is in aisle B02, rack R01, shelf S03."


# --- store info ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("topic", "my", "en"),
    [
        (StoreTopic.CLOSING_TIME, "ဆိုင်က ည ၉ နာရီမှာ ပိတ်ပါတယ်။", "We close at 9 PM."),
        (
            StoreTopic.OPENING_TIME,
            "ဆိုင်က မနက် ၈ နာရီကနေ ည ၉ နာရီအထိ ဖွင့်ပါတယ်။",
            "We're open from 8 AM to 9 PM.",
        ),
        (StoreTopic.PARKING, "ဟုတ်ကဲ့၊ parking ရှိပါတယ်။", "Yes, parking is available."),
        (StoreTopic.RESTROOM, "ဟုတ်ကဲ့၊ အိမ်သာ ရှိပါတယ်။", "Yes, we have a restroom."),
    ],
)
async def test_store_info(make_ctx: MakeContext, topic: StoreTopic, my: str, en: str) -> None:
    record = _record("get_store_info", await get_store_info(make_ctx(), topic), topic=topic)
    assert render_answer(record, "my-MM") == my
    assert render_answer(record, "en-US") == en


async def test_no_parking(make_ctx: MakeContext) -> None:
    result = await get_store_info(make_ctx(store_id="STORE-002"), StoreTopic.PARKING)
    record = _record("get_store_info", result)
    assert render_answer(record, "en-US") == "Sorry, this store has no parking."


async def test_customer_service_burmese_left_to_llm(make_ctx: MakeContext) -> None:
    result = await get_store_info(make_ctx(), StoreTopic.CUSTOMER_SERVICE)
    record = _record("get_store_info", result)
    assert render_answer(record, "my-MM") is None  # sheet text is English: LLM translates
    assert render_answer(record, "en-US") == (
        "Customer service is at the ground floor near the main entrance."
    )


async def test_general_topic_left_to_llm(make_ctx: MakeContext) -> None:
    record = _record("get_store_info", await get_store_info(make_ctx(), StoreTopic.GENERAL))
    assert render_answer(record, "en-US") is None


# --- BMI, FAQ, errors -------------------------------------------------------------------------


def test_bmi_answers() -> None:
    record = _record("calculate_bmi", bmi_from_measurements(height_cm=170, weight_kg=70))
    assert render_answer(record, "my-MM") == "BMI က 24.2 ဖြစ်ပြီး ပုံမှန်အလေးချိန် ဖြစ်ပါတယ်။"
    assert render_answer(record, "en-US") == "Your BMI is 24.2, which is in the normal range."


@pytest.mark.parametrize(
    ("given", "en"),
    [
        ({"height_cm": 170}, "What is your weight, in kilograms or pounds?"),
        ({"weight_kg": 70}, "How tall are you, in centimeters or feet and inches?"),
        ({}, "Please tell me your height and weight."),
    ],
)
def test_bmi_missing(given: dict[str, float], en: str) -> None:
    record = _record("calculate_bmi", bmi_from_measurements(**given))
    assert render_answer(record, "en-US") == en
    assert render_answer(record, "my-MM")


async def test_faq_left_to_llm(make_ctx: MakeContext) -> None:
    record = _record("search_faq", await search_faq(make_ctx(), "return policy"))
    assert record.action is ToolAction.FAQ
    assert render_answer(record, "en-US") is None


def test_failed_call_left_to_llm() -> None:
    record = ToolCallRecord(name="search_product", arguments={}, error="tool_failed")
    assert render_answer(record, "en-US") is None


# --- Agents SDK hook ----------------------------------------------------------------------------


async def test_hook_ends_run_with_template(make_ctx: MakeContext) -> None:
    context = AgentRunContext(tools=make_ctx(language="en-US"))
    context.calls.append(
        _record("calculate_bmi", bmi_from_measurements(height_cm=170, weight_kg=70))
    )
    wrapper = SimpleNamespace(context=context)
    result = finish_with_template(wrapper, [object()])  # type: ignore[arg-type,list-item]
    assert result.is_final_output and str(result.final_output).startswith("Your BMI is 24.2")
    assert context.templated


async def test_hook_defers_to_llm_for_parallel_calls(make_ctx: MakeContext) -> None:
    context = AgentRunContext(tools=make_ctx())
    record = _record("calculate_bmi", bmi_from_measurements(height_cm=170, weight_kg=70))
    context.calls += [record, record]
    wrapper = SimpleNamespace(context=context)
    result = finish_with_template(wrapper, [object(), object()])  # type: ignore[arg-type,list-item]
    assert not result.is_final_output and not context.templated
