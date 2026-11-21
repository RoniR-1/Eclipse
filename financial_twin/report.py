def build_comparison_bundle(paths, *, baseline_path, scenario_record, assumptions,
                            comparisons, limitations):
    reports = {}
    for name, path in paths.items():
        rows = path.get("timeline", [])
        cash_errors = [round(row["cash_chf"] - previous["cash_chf"] - row["cash_change_after_transfers_chf"], 2)
                       for previous, row in zip(rows, rows[1:])
                       if all(key in row for key in ("cash_chf", "cash_change_after_transfers_chf"))
                       and "cash_chf" in previous]
        cash_available = len(rows) > 1 and len(cash_errors) == len(rows) - 1
        flow_errors = []
        for previous, row in zip(rows, rows[1:]):
            if all(key in row for key in ("income_chf", "expenses_chf", "one_off_chf", "debt_payment_chf", "deposit_chf", "cash_chf")):
                expected = row["income_chf"] - row["expenses_chf"] - row["one_off_chf"] - row["debt_payment_chf"] - row["deposit_chf"]
            elif all(key in row for key in ("income_chf", "expenses_chf", "one_off_costs_chf", "debt_payments_chf", "investment_contribution_chf", "cash_chf")):
                expected = row["income_chf"] - row["expenses_chf"] - row["one_off_costs_chf"] - row["debt_payments_chf"] - row["investment_contribution_chf"] + row.get("borrowing_proceeds_chf", 0)
            else:
                continue
            flow_errors.append(round(row["cash_chf"] - previous["cash_chf"] - expected, 2))
        flow_available = len(rows) > 1 and len(flow_errors) == len(rows) - 1
        worth_errors = []
        for row in rows:
            if all(key in row for key in ("total_assets_chf", "debt_chf", "net_worth_chf")):
                expected = row["total_assets_chf"] - row["debt_chf"]
            elif all(key in row for key in ("cash_chf", "investments_chf", "deposits_chf", "property_asset_chf", "debt_chf", "net_worth_chf")):
                expected = row["cash_chf"] + row["investments_chf"] + row["deposits_chf"] + row["property_asset_chf"] - row["debt_chf"]
            else:
                continue
            worth_errors.append(round(row["net_worth_chf"] - expected, 2))
        worth_available = bool(rows) and len(worth_errors) == len(rows)
        drivers = path.get("sensitivity", [])
        reports[name] = {
            "metrics": path.get("metrics", {}), "events": path.get("events", []),
            "commitments": path.get("commitments", path.get("event_precedence", [])),
            "provenance": path.get("provenance", {}),
            "sensitivity_summary": {"driver_count": len(drivers), "drivers": drivers},
            "sanity_checks": [
                {"name": "cash_reconciliation", "available": cash_available,
                 "maximum_error_chf": max(map(abs, cash_errors), default=None),
                 "passed": max(map(abs, cash_errors)) <= 0.01 if cash_available else None},
                {"name": "cash_flow_components", "available": flow_available,
                 "maximum_error_chf": max(map(abs, flow_errors), default=None),
                 "passed": max(map(abs, flow_errors)) <= 0.05 if flow_available else None},
                {"name": "net_worth_reconciliation", "available": worth_available,
                 "maximum_error_chf": max(map(abs, worth_errors), default=None),
                 "passed": max(map(abs, worth_errors)) <= 0.03 if worth_available else None},
            ],
        }
    return {"version": 1, "baseline_path": baseline_path, "scenario_record": scenario_record,
            "assumptions": assumptions, "comparisons": comparisons, "limitations": limitations,
            "paths": reports}
