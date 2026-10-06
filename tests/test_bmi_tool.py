import math

import pytest

from app.models.tools import BmiCategory, BmiResult, MissingInformationResult, ToolAction
from app.tools.bmi_tool import bmi_from_measurements, calculate_bmi
from app.tools.context import ToolInputError


def test_170cm_70kg() -> None:
    result = calculate_bmi(170, 70)
    assert result.bmi == 24.2
    assert result.category is BmiCategory.NORMAL
    assert result.action is ToolAction.BMI


@pytest.mark.parametrize(
    ("height", "weight", "bmi", "category"),
    [
        (170, 50, 17.3, BmiCategory.UNDERWEIGHT),
        (170, 80, 27.7, BmiCategory.OVERWEIGHT),
        (170, 95, 32.9, BmiCategory.OBESE),
        (165.5, 60.2, 22.0, BmiCategory.NORMAL),
    ],
)
def test_categories(height: float, weight: float, bmi: float, category: BmiCategory) -> None:
    result = calculate_bmi(height, weight)
    assert (result.bmi, result.category) == (bmi, category)


def test_category_matches_rounded_value() -> None:
    # 99.84 / 2.0² = 24.96, spoken as 25.0, so it must be OVERWEIGHT, not NORMAL.
    result = calculate_bmi(200, 99.84)
    assert result.bmi == 25.0 and result.category is BmiCategory.OVERWEIGHT


@pytest.mark.parametrize(
    ("height", "weight"),
    [(0, 70), (-170, 70), (300, 70), (170, 0), (170, 500), (math.nan, 70), (170, math.inf)],
)
def test_invalid_measurements_rejected(height: float, weight: float) -> None:
    with pytest.raises(ToolInputError):
        calculate_bmi(height, weight)


@pytest.mark.parametrize(
    ("given", "missing"),
    [
        ({"height_cm": 170}, ["weight"]),
        ({"weight_kg": 70}, ["height"]),
        ({}, ["height", "weight"]),
    ],
)
def test_missing_measurements_reported(given: dict[str, float], missing: list[str]) -> None:
    result = bmi_from_measurements(**given)
    assert isinstance(result, MissingInformationResult)
    assert result.missing == missing
    assert result.action is ToolAction.NEED_MORE_INFORMATION


def test_feet_inches_and_pounds_converted() -> None:
    # 5 ft 7 in = 170.2 cm, 150 lb = 68.0 kg
    result = bmi_from_measurements(height_feet=5, height_inches=7, weight_lb=150)
    assert isinstance(result, BmiResult)
    assert (result.height_cm, result.weight_kg, result.bmi) == (170.2, 68.0, 23.5)


def test_feet_only_height() -> None:
    result = bmi_from_measurements(height_feet=6, weight_kg=80)
    assert isinstance(result, BmiResult) and result.height_cm == 182.9


def test_metric_values_take_precedence() -> None:
    result = bmi_from_measurements(height_cm=170, weight_kg=70, height_feet=5, weight_lb=300)
    assert isinstance(result, BmiResult) and result.bmi == 24.2
