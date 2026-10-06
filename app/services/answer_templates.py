"""Spoken answers rendered from tool results in Python.

For the common, well-defined outcomes (where is X, closing time, parking, BMI,
"what's your weight?") the agent's second LLM call would only rephrase the tool
result. Rendering it here saves that call (~1 s), and the Burmese wording is
controlled exactly. Anything not covered returns None, and the LLM answers as usual.
"""

from collections.abc import Callable, Sequence
from typing import Any

from app.models.kiosk import Language
from app.models.tools import ToolAction
from app.services.agent_tools import ToolCallRecord as Record

_MM_DIGITS = str.maketrans("0123456789", "၀၁၂၃၄၅၆၇၈၉")

_BMI_CATEGORY: dict[Language, dict[str, str]] = {
    "my-MM": {
        "UNDERWEIGHT": "ကိုယ်အလေးချိန်နည်း",
        "NORMAL": "ပုံမှန်အလေးချိန်",
        "OVERWEIGHT": "ကိုယ်အလေးချိန်ပို",
        "OBESE": "အဝလွန်",
    },
    "en-US": {
        "UNDERWEIGHT": "the underweight range",
        "NORMAL": "the normal range",
        "OVERWEIGHT": "the overweight range",
        "OBESE": "the obese range",
    },
}


def mm_number(value: int) -> str:
    return str(value).translate(_MM_DIGITS)


def spoken_time(hhmm: str, language: Language) -> str:
    """'21:00' -> 'ည ၉ နာရီ' / '9 PM'; '08:30' -> 'မနက် ၈ နာရီခွဲ' / '8:30 AM'."""
    hour, minute = (int(part) for part in hhmm.split(":")[:2])
    hour12 = hour % 12 or 12
    if language == "en-US":
        suffix = "AM" if hour < 12 else "PM"
        return f"{hour12} {suffix}" if minute == 0 else f"{hour12}:{minute:02d} {suffix}"
    if 4 <= hour < 12:
        period = "မနက်"
    elif 12 <= hour < 16:
        period = "နေ့လည်"
    elif 16 <= hour < 19:
        period = "ညနေ"
    else:
        period = "ည"
    if minute == 0:
        minutes = ""
    elif minute == 30:
        minutes = "ခွဲ"
    else:
        minutes = f" {mm_number(minute)} မိနစ်"
    return f"{period} {mm_number(hour12)} နာရီ{minutes}"


# --- products ---------------------------------------------------------------------


def _location(product: dict[str, Any], language: Language, *, shelf: bool = True) -> str:
    parts = [("Aisle", product.get("aisle")), ("Rack", product.get("rack"))]
    if shelf:
        parts.append(("Shelf", product.get("shelf")))
    present = [(label, value) for label, value in parts if value]
    if language == "en-US":
        return ", ".join(f"{label.lower()} {value}" for label, value in present)
    return "၊ ".join(f"{label} {value}" for label, value in present)


def _price(product: dict[str, Any], language: Language) -> str:
    price = product.get("price")
    if price is None:
        return ""
    if language == "en-US":
        return f" It costs {price:,} kyats."
    return f" ဈေးနှုန်းက {price:,} ကျပ် ပါ။"


def _stock_notes(products: Sequence[dict[str, Any]], language: Language, single: bool) -> str:
    notes = []
    for product in products:
        status, name = product.get("availability"), product["product_name"]
        if status == "OUT_OF_STOCK":
            if language == "en-US":
                notes.append(
                    " It is currently out of stock." if single else f" {name} is out of stock."
                )
            else:
                notes.append(
                    " ဒါပေမယ့် လက်ရှိ ပစ္စည်းပြတ်နေပါတယ်။" if single else f" {name} ကတော့ ပစ္စည်းပြတ်နေပါတယ်။"
                )
        elif status == "LOW_STOCK":
            if language == "en-US":
                notes.append(" Only a few are left." if single else f" {name} is running low.")
            else:
                notes.append(
                    " နည်းနည်းပဲ ကျန်ပါတော့တယ်။" if single else f" {name} ကတော့ နည်းနည်းပဲ ကျန်ပါတော့တယ်။"
                )
    return "".join(notes)


def _product_found(record: Record, language: Language) -> str | None:
    products: list[dict[str, Any]] = record.data.get("products") or []
    if not products:
        return None
    total = record.data.get("total_matches", len(products))
    price_requested = bool(record.arguments.get("price_requested"))

    if len(products) == 1:
        product = products[0]
        name = product["product_name"]
        if language == "en-US":
            text = f"{name} is in {_location(product, language)}."
        else:
            text = f"{name} ကို {_location(product, language)} မှာ ရှာနိုင်ပါတယ်။"
        text += _stock_notes(products, language, single=True)
        return text + (_price(product, language) if price_requested else "")

    if price_requested:
        return None  # several prices: let the LLM phrase it
    label = str(record.arguments.get("query") or products[0].get("brand") or "")
    aisles = {p.get("aisle") for p in products}
    racks = {(p.get("aisle"), p.get("rack")) for p in products}
    if len(aisles) == 1:
        # Same aisle: send the customer there; the products are side by side.
        if len(racks) == 1:
            location = _location(products[0], language, shelf=False)
        else:
            aisle = products[0].get("aisle")
            location = f"aisle {aisle}" if language == "en-US" else f"Aisle {aisle}"
        if language == "en-US":
            text = f"We have {total} {label} products in {location}."
        else:
            text = f"{label} {mm_number(total)} မျိုးကို {location} မှာ ရှာနိုင်ပါတယ်။"
        return text + _stock_notes(products, language, single=False)

    # Different aisles: name the two best matches.
    first, second = products[0], products[1]
    more = total - 2
    if language == "en-US":
        text = (
            f"{first['product_name']} is in {_location(first, language)}, and "
            f"{second['product_name']} is in {_location(second, language)}."
        )
        text += f" There are {more} more options." if more > 0 else ""
    else:
        text = (
            f"{first['product_name']} ကို {_location(first, language)} မှာ၊ "
            f"{second['product_name']} ကို {_location(second, language)} မှာ ရှာနိုင်ပါတယ်။"
        )
        text += f" နောက်ထပ် {mm_number(more)} မျိုးလည်း ရှိပါသေးတယ်။" if more > 0 else ""
    return text + _stock_notes(products[:2], language, single=False)


def _product_not_found(record: Record, language: Language) -> str | None:
    query = str(record.arguments.get("query") or "")
    suggestions: list[dict[str, Any]] = record.data.get("suggestions") or []
    if language == "en-US":
        text = f"Sorry, I couldn't find {query} in this store."
        if suggestions:
            first = suggestions[0]
            text += f" You could try {first['product_name']} in {_location(first, language)}."
        else:
            text += " Please ask at the customer service counter."
        return text
    text = f"တောင်းပန်ပါတယ်၊ {query} ကို ဒီဆိုင်မှာ မတွေ့ပါဘူး။"
    if suggestions:
        first = suggestions[0]
        text += f" {first['product_name']} ကိုတော့ {_location(first, language)} မှာ ရှာနိုင်ပါတယ်။"
    else:
        text += " Customer Service ကောင်တာမှာ မေးမြန်းနိုင်ပါတယ်။"
    return text


# --- store info ----------------------------------------------------------------------


def _yes_no(value: Any, yes: str, no: str) -> str | None:
    if value is None:
        return None
    return yes if value else no


def _store_info(record: Record, language: Language) -> str | None:
    data = record.data
    topic = data.get("topic")
    en = language == "en-US"
    opening, closing = data.get("opening_time"), data.get("closing_time")

    if topic == "CLOSING_TIME" and closing:
        when = spoken_time(closing, language)
        return f"We close at {when}." if en else f"ဆိုင်က {when}မှာ ပိတ်ပါတယ်။"
    if topic == "OPENING_TIME" and opening and closing:
        start, end = spoken_time(opening, language), spoken_time(closing, language)
        return f"We're open from {start} to {end}." if en else (
            f"ဆိုင်က {start}ကနေ {end}အထိ ဖွင့်ပါတယ်။"
        )  # fmt: skip
    if topic == "PARKING":
        return _yes_no(
            data.get("parking_available"),
            "Yes, parking is available." if en else "ဟုတ်ကဲ့၊ parking ရှိပါတယ်။",
            "Sorry, this store has no parking." if en else "တောင်းပန်ပါတယ်၊ ဒီဆိုင်မှာ parking မရှိပါဘူး။",
        )
    if topic == "RESTROOM":
        return _yes_no(
            data.get("restroom_available"),
            "Yes, we have a restroom." if en else "ဟုတ်ကဲ့၊ အိမ်သာ ရှိပါတယ်။",
            "Sorry, this store has no restroom." if en else "တောင်းပန်ပါတယ်၊ ဒီဆိုင်မှာ အိမ်သာ မရှိပါဘူး။",
        )
    if topic == "CUSTOMER_SERVICE" and en and data.get("customer_service_location"):
        # The location text in the sheet is English; for Burmese the LLM translates it.
        return f"Customer service is at the {data['customer_service_location'].lower()}."
    return None


# --- BMI -----------------------------------------------------------------------------


def _bmi(record: Record, language: Language) -> str | None:
    bmi, category = record.data.get("bmi"), record.data.get("category")
    if bmi is None or category not in _BMI_CATEGORY[language]:
        return None
    label = _BMI_CATEGORY[language][category]
    if language == "en-US":
        return f"Your BMI is {bmi}, which is in {label}."
    return f"BMI က {bmi} ဖြစ်ပြီး {label} ဖြစ်ပါတယ်။"


def _bmi_missing(record: Record, language: Language) -> str | None:
    if record.name != "calculate_bmi":
        return None
    missing = set(record.data.get("missing") or [])
    en = language == "en-US"
    if missing == {"weight"}:
        return (
            "What is your weight, in kilograms or pounds?"
            if en
            else "ကိုယ်အလေးချိန် ဘယ်လောက်ရှိပါသလဲ။ kg ဒါမှမဟုတ် ပေါင်နဲ့ ပြောပေးပါ။"
        )
    if missing == {"height"}:
        return (
            "How tall are you, in centimeters or feet and inches?"
            if en
            else "အရပ် ဘယ်လောက်ရှိပါသလဲ။ cm ဒါမှမဟုတ် ပေ၊ လက်မနဲ့ ပြောပေးပါ။"
        )
    if missing == {"height", "weight"}:
        return (
            "Please tell me your height and weight." if en else "BMI တွက်ဖို့ အရပ်နဲ့ ကိုယ်အလေးချိန်ကို ပြောပေးပါ။"
        )
    return None


_RENDERERS: dict[ToolAction, Callable[[Record, Language], str | None]] = {
    ToolAction.PRODUCT_LOCATION: _product_found,
    ToolAction.PRODUCT_NOT_FOUND: _product_not_found,
    ToolAction.STORE_INFO: _store_info,
    ToolAction.BMI: _bmi,
    ToolAction.NEED_MORE_INFORMATION: _bmi_missing,
    # FAQ is left to the LLM: it checks the approved answer actually fits the question.
}


def render_answer(record: Record, language: Language) -> str | None:
    """Spoken answer for a single successful tool call, or None to let the LLM answer."""
    if record.error is not None or record.action is None:
        return None
    renderer = _RENDERERS.get(record.action)
    return renderer(record, language) if renderer else None
