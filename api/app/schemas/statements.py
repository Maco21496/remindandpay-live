# api/app/schemas/statements.py
from typing import List, Optional
from pydantic import BaseModel


class OpenInvoiceOut(BaseModel):
    id: int
    ref: str
    desc: str
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    total: float
    paid_to_date: float
    outstanding: float
    days_overdue: int


class BucketsOut(BaseModel):
    overdue_0_30: float
    overdue_31_60: float
    overdue_61_90: float
    overdue_90p: float


class TotalsOut(BaseModel):
    total_outstanding_gross: float
    unallocated_credits: float
    balance_due: float
    overdue_total: float


class StatementOut(BaseModel):
    as_of: str
    totals: TotalsOut
    buckets: BucketsOut
    open_invoices: List[OpenInvoiceOut]

