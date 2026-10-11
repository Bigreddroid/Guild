"""A demo vendor site the Web Operator's cloud browser books transfers on.

This is Guild's own page, labelled as a demo vendor, so the KERNEL browser has a
stable real website to drive (no bot blocking, no scraping someone else's site).
Quotes are a fixed function of the passenger count, so every run is the same.
"""
from __future__ import annotations

import html
import math
from datetime import date
from typing import Optional

PATH = "/vendor/lisbon-transfers"
MAX_PASSENGERS = 12


class VendorInputError(ValueError):
    pass


def quotes(passengers: int) -> list:
    """(provider, price in EUR, pickup time) for a group of this size."""
    vans = math.ceil(passengers / 8)
    cars = math.ceil(passengers / 4)
    return [
        ("Lisbon Transfers, private van", 96 * vans, "09:30"),
        ("Airport Shuttle Co, shared shuttle", 12 * passengers, "10:15"),
        (f"Taxi Lisboa, {cars} car{'s' if cars > 1 else ''}", 40 * cars, "09:45"),
    ]


def parse(passengers: Optional[str], day: Optional[str]) -> Optional[tuple]:
    """None when no search was made; raises VendorInputError on bad input."""
    if passengers is None and day is None:
        return None
    try:
        n = int(passengers or "")
    except ValueError:
        raise VendorInputError("passengers must be a whole number")
    if not 1 <= n <= MAX_PASSENGERS:
        raise VendorInputError(f"passengers must be 1 to {MAX_PASSENGERS}")
    try:
        d = date.fromisoformat(day or "")
    except ValueError:
        raise VendorInputError("date must be YYYY-MM-DD")
    return n, d


def page(search: Optional[tuple] = None, error: str = "") -> str:
    n, d = search if search else (6, date(2026, 11, 2))
    rows = ""
    if search:
        rows = "".join(
            f'<tr data-testid="quote"><td data-testid="provider">{html.escape(p)}</td>'
            f'<td data-testid="price" data-amount="{price}">€{price}</td>'
            f'<td data-testid="pickup">{t}</td></tr>'
            for p, price, t in quotes(n))
        rows = (f'<h2>{n} passenger{"s" if n > 1 else ""}, {d.isoformat()}</h2>'
                f'<table><thead><tr><th>Provider</th><th>Price</th><th>Pickup</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>')
    err = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lisbon Airport Transfers (demo vendor)</title>
<style>
body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#fff8f0;color:#2b2118}}
main{{max-width:640px;margin:0 auto;padding:16px}}
.banner{{background:#2b2118;color:#fff;padding:8px 16px;font-size:13px}}
h1{{font-size:24px;margin:16px 0 4px}} .sub{{color:#7a6552;margin:0 0 16px}}
form{{display:flex;gap:12px;flex-wrap:wrap;align-items:end;background:#fff;padding:16px;border-radius:12px;border:1px solid #eadbc8}}
label{{display:flex;flex-direction:column;font-size:13px;gap:4px}}
input{{font-size:16px;padding:8px;border:1px solid #cdb79e;border-radius:8px}}
button{{font-size:16px;padding:9px 18px;border:0;border-radius:8px;background:#d9480f;color:#fff;cursor:pointer}}
table{{width:100%;border-collapse:collapse;margin-top:8px;background:#fff}}
th,td{{text-align:left;padding:10px;border-bottom:1px solid #eadbc8}} h2{{font-size:17px;margin:20px 0 4px}}
.err{{color:#b42318;font-weight:600}}
</style></head><body>
<div class="banner">Demo vendor for Guild. Not a real company; nothing here is booked or charged.</div>
<main>
<h1>Lisbon Airport Transfers</h1>
<p class="sub">Airport (LIS) to city centre. Compare group quotes.</p>
{err}
<form method="get" action="{PATH}">
<label>Passengers<input data-testid="passengers" name="passengers" type="number" min="1" max="{MAX_PASSENGERS}" value="{n}" required></label>
<label>Date<input data-testid="date" name="date" type="date" value="{d.isoformat()}" required></label>
<button data-testid="search" type="submit">Search</button>
</form>
{rows}
</main></body></html>"""


# Runs inside KERNEL's cloud browser via /browsers/{id}/playwright/execute, where
# `page` is in scope and the returned value comes back as `result`. The URL and
# date are inserted as JSON literals, never by pasting raw text into code.
KERNEL_SCRIPT = """
const url = __URL__;
await page.goto(url, { waitUntil: 'domcontentloaded' });
await page.fill('[data-testid=passengers]', __PASSENGERS__);
await page.fill('[data-testid=date]', __DATE__);
await Promise.all([page.waitForURL(/passengers=/), page.click('[data-testid=search]')]);
await page.waitForSelector('[data-testid=quote]');
return await page.$$eval('[data-testid=quote]', rows => rows.map(r => ({
  provider: r.querySelector('[data-testid=provider]').textContent.trim(),
  price: r.querySelector('[data-testid=price]').dataset.amount,
  pickup: r.querySelector('[data-testid=pickup]').textContent.trim(),
})));
"""


def script(url: str, passengers: int = 6, day: str = "2026-11-02") -> str:
    import json
    return (KERNEL_SCRIPT.replace("__URL__", json.dumps(url))
            .replace("__PASSENGERS__", json.dumps(str(passengers)))
            .replace("__DATE__", json.dumps(day)))
