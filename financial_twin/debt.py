import math


def fixed_payment(principal, annual_rate, months):
    rate = annual_rate / 12
    if rate == 0:
        return principal / months
    return principal * rate / -math.expm1(-months * math.log1p(rate))


def advance_loan(loan):
    opening = loan["principal_chf"]
    interest = opening * loan["annual_interest_rate"] / 12
    payment = min(opening + interest, loan["monthly_payment_chf"])
    if 0 < opening + interest - payment < 1e-9:
        payment = opening + interest
    interest_paid = min(payment, interest)
    principal_paid = payment - interest_paid
    capitalized = interest - interest_paid
    closing = max(0.0, opening + interest - payment)
    loan["principal_chf"] = closing
    return {"name": loan.get("name", loan.get("id")), "type": loan.get("type"), "opening_balance_chf": opening,
            "interest_accrued_chf": interest, "payment_chf": payment,
            "interest_paid_chf": interest_paid, "principal_paid_chf": principal_paid,
            "capitalized_interest_chf": capitalized, "closing_balance_chf": closing}
