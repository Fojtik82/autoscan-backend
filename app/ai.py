# app/ai.py
import json
from typing import List, Dict, Optional
from openai import OpenAI
from .config import OPENAI_API_KEY, OPENAI_MODEL

# klient vytvoříme jen pokud je k dispozici klíč
client: Optional[OpenAI] = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


async def ai_estimate(
    rows: List[Dict],
    brand: str,
    model: str,
    year: int,
    mileage: int,
    fuel: str = "",
    motor: str = "",
) -> dict:
    """
    Volá OpenAI (chat.completions) a vrací AI odhad ceny z poskytnutých comps.
    """
    if not client:
        return {"error": "No OPENAI_API_KEY configured"}

    # pošli jen to nejnutnější a max 20 záznamů kvůli nákladům
    slim = []
    for r in rows[:20]:
        slim.append({
            "source": r.get("source"),
            "price_czk": r.get("price_czk"),
            "year": r.get("year"),
            "mileage": r.get("mileage"),
            "fuel": r.get("fuel"),
            "motor": r.get("motor"),
        })

    comps_json = json.dumps(slim, ensure_ascii=False)

    user_prompt = f"""
Jsi analytik trhu s ojetými vozy v ČR. Dostaneš parametry cílového vozu a seznam nejbližších srovnatelných nabídek.
Zohledni nájezd a rok (můžeš vážit), ignoruj extrémní outliery. Vrať čistý JSON bez dalšího textu.

Cílový vůz:
- značka: {brand}
- model: {model}
- rok: {year}
- nájezd: {mileage} km
- palivo: {fuel}
- motor: {motor}

Srovnatelné nabídky (JSON):
{comps_json}

Odpověz výhradně tímto JSONem:
{{"low_czk": int, "price_czk": int, "high_czk": int}}
""".strip()

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,             # např. "gpt-4o-mini"
        messages=[
            {"role": "system", "content": "Jsi expert na oceňování vozidel, odpovídej přesně a stroze."},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )

    try:
        content = (resp.choices[0].message.content or "").strip()
        data = json.loads(content)
        # rychlá sanitizace výstupu
        return {
            "low_czk": int(data.get("low_czk")),
            "price_czk": int(data.get("price_czk")),
            "high_czk": int(data.get("high_czk")),
            "used_comps": len(slim),
        }
    except Exception as e:
        return {"error": f"AI response parse failed: {e}"}
