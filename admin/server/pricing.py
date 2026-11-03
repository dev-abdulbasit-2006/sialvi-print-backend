"""
Price calculation (server-side only - the User app never computes money).

A print type has a base price_per_page. It may also have "package" tiers
by total quantity (pages x copies), e.g.
    1-3 pages   -> Rs 40 per page
    4-10 pages  -> Rs 30 per page
    11 and above -> Rs 25 per page
The tier that matches the order's total quantity sets the per-page rate
for the WHOLE order (the same "volume price" convention as the shop's
printed price sheet). If no tier matches, the base price is used.
"""

import sqlite3


def get_tiers(conn, profile_id: int) -> list[dict]:
    query = ("SELECT id, min_qty, max_qty, price_per_page FROM price_tiers "
             "WHERE profile_id = ? ORDER BY min_qty ASC")
    try:
        rows = conn.execute(query, (profile_id,)).fetchall()
    except sqlite3.OperationalError as e:
        # An older database can be missing the table OR have it with the wrong
        # columns ("no such column: min_qty"). Repair it, then try once more.
        if "no such table" not in str(e) and "no such column" not in str(e):
            raise
        from admin.database.db import ensure_price_tiers_table, repair_now
        ensure_price_tiers_table(conn)
        repair_now(conn)
        rows = conn.execute(query, (profile_id,)).fetchall()
    return [dict(r) for r in rows]


def unit_price(base_price: float, tiers: list[dict], quantity: int) -> float:
    best = None
    for t in tiers:
        if quantity >= t["min_qty"] and (t["max_qty"] is None or quantity <= t["max_qty"]):
            if best is None or t["min_qty"] > best["min_qty"]:
                best = t          # overlapping tiers: the more specific (higher start) wins
    return float(best["price_per_page"]) if best else float(base_price)


def compute_total(base_price: float, tiers: list[dict], quantity: int) -> tuple[float, float]:
    """Returns (unit_price, total_price)."""
    unit = unit_price(base_price, tiers, quantity)
    return unit, round(unit * quantity, 2)


def describe_tier(t: dict) -> str:
    hi = "and above" if t["max_qty"] is None else f"to {t['max_qty']}"
    return f"{t['min_qty']} {hi}: Rs {t['price_per_page']:g}/page"
