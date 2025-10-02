from typing import List, Dict, Optional
import math

def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1

def estimate_from_rows(rows: List[Dict], target_year: int, target_mileage: int, motor_hint: str = "") -> Optional[Dict]:
    """Z cen v `rows` spočítá robustní odhad: median, low/high (IQR) + count."""
    prices = []
    weights = []

    # volitelné jemné vážení
    motor_hint = (motor_hint or "").strip().lower()
    for r in rows:
        price = r.get("price_czk")
        if price is None:
            continue
        try:
            price = float(price)
        except:
            continue

        # baseline weight = 1.0
        w = 1.0

        # menší penalizace vzdálenosti km/rok od targetu
        y = r.get("year")
        m = r.get("mileage")
        if isinstance(y, int):
            w *= 1.0 / (1.0 + abs(y - target_year) * 0.1)
        if isinstance(m, int):
            w *= 1.0 / (1.0 + abs(m - target_mileage) / 50000.0)

        # lehké zvýhodnění shody motoru
        motor = (r.get("motor") or "").lower()
        if motor_hint and motor_hint in motor:
            w *= 1.15

        prices.append(price)
        weights.append(w)

    if not prices:
        return None

    # seřadit podle ceny, ať víme kvantily
    paired = sorted(zip(prices, weights), key=lambda x: x[0])
    sp = [p for p, _ in paired]

    # medián + IQR (25. a 75. percentil)
    median = _percentile(sp, 0.5)
    p25 = _percentile(sp, 0.25)
    p75 = _percentile(sp, 0.75)

    return {
        "price_czk": round(median) if not math.isnan(median) else None,
        "low_czk":   round(p25) if not math.isnan(p25) else None,
        "high_czk":  round(p75) if not math.isnan(p75) else None,
        "count":     len(sp),
    }
