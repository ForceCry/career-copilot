WORK_HOURS_PER_DAY = 5.5  # midpoint of a 5-6 hour working day, used to normalize hourly rates
WORK_DAYS_PER_MONTH = 21  # ~5-day week over a month, standard working-month assumption
HOURLY_TO_MONTHLY = WORK_HOURS_PER_DAY * WORK_DAYS_PER_MONTH


def monthly_salary(
    salary_min: float | None, salary_max: float | None, salary_period: str
) -> tuple[float, float] | None:
    """Normalizes a vacancy's salary range to a monthly figure, regardless
    of what period the source actually listed it in - so recommendations
    from different boards (some post annual, some hourly, some monthly)
    are comparable at a glance next to the match score.

    "year" divides by 12. "hour" assumes a 5-6 hour working day (part-time
    schedule), not a standard 8-hour one - per explicit request, not a
    guess at the actual role's hours. "month" and "" (period not given by
    the source) pass through unchanged - treating an unspecified period as
    already-monthly is the most common case in practice, not a guarantee.
    """
    if salary_min is None or salary_max is None:
        return None
    if salary_period == "year":
        return salary_min / 12, salary_max / 12
    if salary_period == "hour":
        return salary_min * HOURLY_TO_MONTHLY, salary_max * HOURLY_TO_MONTHLY
    return salary_min, salary_max
