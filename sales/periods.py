from datetime import date

from django.utils.formats import date_format

from documents.models import ElectronicReceipt, HonorariumReceipt, TaxDocument
from payments.models import Payment
from reports.models import MonthlyClose

from .models import Sale


def shift_month(month, offset):
    index = month.year * 12 + month.month - 1 + offset
    year, zero_based_month = divmod(index, 12)
    if 1 <= year <= 9999:
        return date(year, zero_based_month + 1, 1)
    return None


def period_label(month):
    return date_format(month, "F Y").capitalize()


def activity_months(organization):
    sources = (
        (Sale, "operation_date"),
        (Payment, "payment_date"),
        (ElectronicReceipt, "tax_issue_date"),
        (TaxDocument, "tax_period"),
        (HonorariumReceipt, "issue_date"),
        (MonthlyClose, "period"),
    )
    first, *others = [
        model.objects.filter(organization=organization).dates(field, "month").order_by()
        for model, field in sources
    ]
    return sorted(first.union(*others))


def period_navigation_context(organization, selected):
    history = activity_months(organization)
    activity_by_year = {}
    for month in history:
        activity_by_year.setdefault(month.year, []).append(month.month)
    picker_year = selected.year if selected.year in activity_by_year else max(activity_by_year, default=selected.year)
    previous_month = next((month for month in reversed(history) if month < selected), None)
    next_month = shift_month(selected, 1)
    first_month = history[0].month if history and picker_year == history[0].year else 1
    return {
        "organization": organization,
        "active_year": selected.year,
        "active_month": selected.month,
        "active_label": period_label(selected),
        "picker_year": picker_year,
        "activity_by_year": activity_by_year,
        "first_month": first_month,
        "enabled_months": activity_by_year.get(picker_year, []),
        "previous_period": previous_month.isoformat()[:7] if previous_month else None,
        "previous_period_label": period_label(previous_month) if previous_month else None,
        "next_period": next_month.isoformat()[:7] if next_month else None,
        "next_period_label": period_label(next_month) if next_month else None,
        "months": [
            (number, date_format(date(selected.year, number, 1), "F").capitalize())
            for number in range(1, 13)
        ],
    }
