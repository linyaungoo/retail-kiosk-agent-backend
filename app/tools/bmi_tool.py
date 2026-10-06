"""calculate_bmi: computed in Python, never by the LLM."""

import math

from app.models.tools import BmiCategory, BmiResult, MissingInformationResult, ToolAction
from app.tools.context import ToolInputError

MIN_HEIGHT_CM, MAX_HEIGHT_CM = 50.0, 250.0
MIN_WEIGHT_KG, MAX_WEIGHT_KG = 10.0, 300.0

CM_PER_FOOT = 30.48
CM_PER_INCH = 2.54
KG_PER_POUND = 0.45359237


def _category(bmi: float) -> BmiCategory:
    # WHO adult cut-offs.
    if bmi < 18.5:
        return BmiCategory.UNDERWEIGHT
    if bmi < 25.0:
        return BmiCategory.NORMAL
    if bmi < 30.0:
        return BmiCategory.OVERWEIGHT
    return BmiCategory.OBESE


def calculate_bmi(height_cm: float, weight_kg: float) -> BmiResult:
    if not math.isfinite(height_cm) or not MIN_HEIGHT_CM <= height_cm <= MAX_HEIGHT_CM:
        raise ToolInputError(f"height_cm must be between {MIN_HEIGHT_CM:g} and {MAX_HEIGHT_CM:g}")
    if not math.isfinite(weight_kg) or not MIN_WEIGHT_KG <= weight_kg <= MAX_WEIGHT_KG:
        raise ToolInputError(f"weight_kg must be between {MIN_WEIGHT_KG:g} and {MAX_WEIGHT_KG:g}")

    # Categorise the rounded value so the spoken number and category always agree.
    bmi = round(weight_kg / (height_cm / 100) ** 2, 1)
    return BmiResult(
        action=ToolAction.BMI,
        bmi=bmi,
        category=_category(bmi),
        height_cm=height_cm,
        weight_kg=weight_kg,
    )


def _height_cm(
    height_cm: float | None, height_feet: float | None, height_inches: float | None
) -> float | None:
    if height_cm is not None:
        return height_cm
    if height_feet is None and height_inches is None:
        return None
    return round((height_feet or 0) * CM_PER_FOOT + (height_inches or 0) * CM_PER_INCH, 1)


def _weight_kg(weight_kg: float | None, weight_lb: float | None) -> float | None:
    if weight_kg is not None:
        return weight_kg
    return None if weight_lb is None else round(weight_lb * KG_PER_POUND, 1)


def bmi_from_measurements(
    *,
    height_cm: float | None = None,
    weight_kg: float | None = None,
    height_feet: float | None = None,
    height_inches: float | None = None,
    weight_lb: float | None = None,
) -> BmiResult | MissingInformationResult:
    """BMI from whatever the customer said; reports what is still missing.

    Feet/inches and pounds are common in Myanmar, so they are converted here
    rather than asking the model to do arithmetic.
    """
    height = _height_cm(height_cm, height_feet, height_inches)
    weight = _weight_kg(weight_kg, weight_lb)
    missing = [name for name, value in (("height", height), ("weight", weight)) if value is None]
    if missing:
        return MissingInformationResult(missing=missing)
    assert height is not None and weight is not None
    return calculate_bmi(height, weight)
