from datetime import datetime, timedelta

def end_of_next_month(dt: datetime) -> datetime:
    # First day of next month
    y = dt.year + (1 if dt.month == 12 else 0)
    m = 1 if dt.month == 12 else dt.month + 1
    first_next = datetime(y, m, 1, dt.hour, dt.minute, dt.second, tzinfo=dt.tzinfo)
    # First day of the month after that, minus one day = last day of next month
    y2 = y + (1 if m == 12 else 0)
    m2 = 1 if m == 12 else m + 1
    first_after = datetime(y2, m2, 1, dt.hour, dt.minute, dt.second, tzinfo=dt.tzinfo)
    return first_after - timedelta(days=1)

def compute_due_date(issue: datetime, terms_type: str, terms_days: int | None) -> datetime:
    """Return the due date based on terms."""
    if terms_type == "net_30":
        return issue + timedelta(days=30)
    if terms_type == "net_60":
        return issue + timedelta(days=60)
    if terms_type == "month_following":
        return end_of_next_month(issue)
    if terms_type == "custom" and terms_days:
        return issue + timedelta(days=int(terms_days))
    # sensible default
    return issue + timedelta(days=30)
