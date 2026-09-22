from __future__ import annotations

from typing import Any

from engine.indicators import normalize_strategy_indicators

_ALWAYS_AVAILABLE_FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "oi",
    "current_open",
    "current_high",
    "current_low",
    "current_close",
    "current_volume",
    "Time_Of_Day",
    "Day_Of_Week",
    "Day_of_Week",
    "Day_Name",
    "Hour",
    "Minute",
    "Time_HHMM",
    "Is_Monday",
    "Is_Tuesday",
    "Is_Wednesday",
    "Is_Thursday",
    "Is_Friday",
    # The daily trend (engine.indicators.DAILY_AVERAGE_PERIODS). The engine has
    # computed these for every rule since 86dc83b, but this list never learned
    # them, so saving "close is above Daily_SMA_20" was refused (2026-09-22).
    "Daily_SMA_10",
    "Daily_SMA_20",
    "Daily_SMA_50",
}

_WEEKDAY_NAMES = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}

_TIME_OPERATORS = {"is_below", "is_above", "<=", ">=", "==", "crosses_above", "crosses_below"}
_DAY_OPERATORS = {"contains", "not_contains"}
_NUMERIC_OPERATORS = {"crosses_above", "is_above", "crosses_below", "is_below", "touches", ">=", "<=", "=="}
_BOOLEAN_OPERATORS = {"==", "is_true", "is_false"}

_GENERIC_DAILY_CPR_FIELDS = {
    "pivot",
    "bc",
    "tc",
    "cpr_width_pct",
    "cpr_is_narrow",
    "cpr_is_moderate",
    "cpr_is_wide",
    "R0.5",
    "R1",
    "R1.5",
    "R2",
    "R2.5",
    "R3",
    "R3.5",
    "R4",
    "R4.5",
    "R5",
    "S0.5",
    "S1",
    "S1.5",
    "S2",
    "S2.5",
    "S3",
    "S3.5",
    "S4",
    "S4.5",
    "S5",
}

_CPR_LEVELS = [
    "Pivot",
    "TC",
    "BC",
    "width_pct",
    "is_narrow",
    "is_moderate",
    "is_wide",
    "R0.5",
    "R1",
    "R1.5",
    "R2",
    "R2.5",
    "R3",
    "R3.5",
    "R4",
    "R4.5",
    "R5",
    "S0.5",
    "S1",
    "S1.5",
    "S2",
    "S2.5",
    "S3",
    "S3.5",
    "S4",
    "S4.5",
    "S5",
]


def _cpr_timeframe(indicator_id: str) -> str:
    parts = str(indicator_id).split("_")
    if len(parts) > 1 and parts[1].endswith("m"):
        return "D"
    return (parts[3] if len(parts) > 3 else "D").upper()


def _cpr_field_prefix(indicator_id: str) -> str:
    tf = _cpr_timeframe(indicator_id)
    return "CPR_" if tf == "D" else f"CPR_{tf}_"


def collect_condition_fields(indicators: list[str] | None) -> set[str]:
    fields = set(_ALWAYS_AVAILABLE_FIELDS)

    for indicator_id in indicators or []:
        if not isinstance(indicator_id, str) or not indicator_id:
            continue

        fields.add(indicator_id)

        if indicator_id == "Previous_Day":
            fields.update({"Yesterday_Open", "Yesterday_High", "Yesterday_Low", "Yesterday_Close"})
            continue

        if indicator_id.startswith("Signal_Candle"):
            fields.update({"Signal_Candle_Open", "Signal_Candle_High", "Signal_Candle_Low", "Signal_Candle_Close"})
            continue

        if indicator_id.startswith("ORB_"):
            fields.update(
                {
                    "ORB_High",
                    "ORB_Low",
                    "ORB_Range",
                    "ORB_is_breakout_up",
                    "ORB_is_breakout_down",
                    "ORB_is_inside",
                    "ORB_Breakout_Up",
                    "ORB_Breakout_Down",
                    "ORB_Inside",
                }
            )
            continue

        if indicator_id.startswith("CPR"):
            prefix = _cpr_field_prefix(indicator_id)
            fields.update({f"{prefix}{level}" for level in _CPR_LEVELS})
            if prefix == "CPR_":
                fields.update(_GENERIC_DAILY_CPR_FIELDS)
            continue

        if indicator_id.startswith("MACD_"):
            fields.update(
                {f"{indicator_id}_signal", f"{indicator_id}_histogram", "MACD_line", "MACD_signal", "MACD_histogram"}
            )
            continue

        if indicator_id.startswith("BB_"):
            fields.update(
                {
                    f"{indicator_id}_upper",
                    f"{indicator_id}_lower",
                    f"{indicator_id}_width",
                    "BB_upper",
                    "BB_middle",
                    "BB_lower",
                    "BB_width",
                }
            )
            continue

        if indicator_id.startswith("StochRSI_"):
            fields.update({f"{indicator_id}_K", f"{indicator_id}_D", "StochRSI_K", "StochRSI_D"})
            continue

        if indicator_id.startswith("ADX_"):
            fields.update({f"{indicator_id}_plus_di", f"{indicator_id}_minus_di", "ADX", "ADX_plus_di", "ADX_minus_di"})
            continue

        if indicator_id.startswith("Supertrend_"):
            fields.add("supertrend_dir")
            continue

        if indicator_id.startswith("VWAP_"):
            fields.add("VWAP")

    return fields


def collect_boolean_condition_fields(indicators: list[str] | None) -> set[str]:
    bool_fields = {
        "ORB_is_breakout_up",
        "ORB_is_breakout_down",
        "ORB_is_inside",
        "ORB_Breakout_Up",
        "ORB_Breakout_Down",
        "ORB_Inside",
        "cpr_is_narrow",
        "cpr_is_moderate",
        "cpr_is_wide",
    }

    for indicator_id in indicators or []:
        if not isinstance(indicator_id, str):
            continue
        if indicator_id.startswith("CPR"):
            prefix = _cpr_field_prefix(indicator_id)
            bool_fields.update({f"{prefix}is_narrow", f"{prefix}is_moderate", f"{prefix}is_wide"})

    return bool_fields


def _is_valid_time_literal(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parts = value.strip().split(":")
    if len(parts) not in {2, 3}:
        return False
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59


def _is_numeric_literal(value: Any) -> bool:
    if value in ("", None):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _logic_value(cond: dict, index: int) -> str:
    if index == 0:
        return "IF"
    return str(cond.get("logic", cond.get("connector", "AND")) or "AND").upper()


def validate_strategy_contract(
    indicators: list[str] | None,
    entry_conditions: list[dict] | None = None,
    exit_conditions: list[dict] | None = None,
) -> dict[str, Any]:
    normalized = normalize_strategy_indicators(
        indicators or [],
        entry_conditions=entry_conditions or [],
        exit_conditions=exit_conditions or [],
    )
    fields = collect_condition_fields(normalized)
    boolean_fields = collect_boolean_condition_fields(normalized)
    errors: list[str] = []
    warnings: list[str] = []

    def validate_group(group_name: str, conditions: list[dict] | None) -> None:
        for index, cond in enumerate(conditions or []):
            label = f"{group_name} condition #{index + 1}"
            if not isinstance(cond, dict):
                errors.append(f"{label}: condition payload is invalid.")
                continue

            logic = _logic_value(cond, index)
            if index > 0 and logic not in {"AND", "OR"}:
                errors.append(f"{label}: unsupported connector '{logic}'.")

            left = cond.get("left")
            operator = cond.get("operator")
            right = cond.get("right")
            if not left:
                errors.append(f"{label}: missing left-hand side.")
                continue
            if not operator:
                errors.append(f"{label}: missing operator.")
                continue

            if left == "Time_Of_Day":
                if operator not in _TIME_OPERATORS:
                    errors.append(f"{label}: operator '{operator}' is not supported for Time_Of_Day.")
                if right != "time":
                    errors.append(f"{label}: Time_Of_Day must compare against a time value.")
                if not _is_valid_time_literal(cond.get("right_time")):
                    errors.append(f"{label}: invalid time value '{cond.get('right_time')}'.")
                continue

            if left == "Day_Of_Week":
                if operator not in _DAY_OPERATORS:
                    errors.append(f"{label}: operator '{operator}' is not supported for Day_Of_Week.")
                if right != "days":
                    errors.append(f"{label}: Day_Of_Week must compare against a day list.")
                days = cond.get("right_days")
                if not isinstance(days, list) or not days:
                    errors.append(f"{label}: select at least one day.")
                else:
                    invalid_days = [day for day in days if day not in _WEEKDAY_NAMES]
                    if invalid_days:
                        errors.append(f"{label}: invalid day values {invalid_days}.")
                continue

            if left not in fields:
                errors.append(f"{label}: unsupported left-hand field '{left}'.")
                continue

            if left in boolean_fields:
                if operator not in _BOOLEAN_OPERATORS:
                    errors.append(f"{label}: operator '{operator}' is not supported for boolean field '{left}'.")
                if operator == "==" and right == "number" and not _is_numeric_literal(cond.get("right_number_value")):
                    errors.append(f"{label}: numeric comparison value is missing.")
                elif operator == "==" and right not in {"true", "false", "number"} and right not in fields:
                    errors.append(f"{label}: unsupported right-hand field '{right}'.")
                continue

            if operator not in _NUMERIC_OPERATORS and operator not in _BOOLEAN_OPERATORS:
                errors.append(f"{label}: unsupported operator '{operator}'.")
                continue

            if operator in {"is_true", "is_false"}:
                errors.append(f"{label}: operator '{operator}' is only valid for boolean fields.")
                continue

            if right == "number":
                if not _is_numeric_literal(cond.get("right_number_value")):
                    errors.append(f"{label}: numeric comparison value is missing.")
                continue

            if right in {"time", "days"}:
                errors.append(f"{label}: right-hand side '{right}' is only valid for time/day conditions.")
                continue

            if right in {"true", "false"}:
                if operator != "==":
                    errors.append(f"{label}: boolean literals can only be used with '=='.")
                continue

            if right not in fields:
                errors.append(f"{label}: unsupported right-hand field '{right}'.")

    validate_group("Entry", entry_conditions)
    validate_group("Exit", exit_conditions)

    if not normalized:
        warnings.append("No indicators configured. Only raw candle/time/day fields will be available.")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_indicators": normalized,
        "available_fields": sorted(fields),
    }
