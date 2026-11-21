"""Display currencies and exchange rates.

The engine always calculates in CHF (the Swiss reference data is in CHF). Other currencies are a view:
amounts are converted to CHF on the way in and back to the chosen currency for display.

Rates are the European Central Bank reference rates, fetched from the Frankfurter service (free, no key)
and kept for a few hours. If the service cannot be reached, the last saved rates are used, and failing that
the built-in rates below, so the app keeps working offline. Every answer says which rates it used.
"""

import json
import math
import os
from pathlib import Path
import re
import threading
from time import monotonic
from urllib.error import URLError
from urllib.request import Request, urlopen

RATES_URL = "https://api.frankfurter.dev/v1/latest?base=CHF"
REFRESH_SECONDS = 6 * 3600  # live rates are kept this long (the ECB publishes once per working day)
RETRY_SECONDS = 300  # after a failed fetch, offline rates are used for this long before trying again
FETCH_TIMEOUT_SECONDS = 5
RATES_FILE = None  # set by server.main(); None keeps tests from reading or writing a file

# code: (name, symbol used for display)
CURRENCIES = {
    "CHF": ("Swiss franc", "CHF"),
    "EUR": ("Euro", "€"),
    "USD": ("US dollar", "$"),
    "GBP": ("British pound", "£"),
    "JPY": ("Japanese yen", "¥"),
    "CNY": ("Chinese yuan", "CN¥"),
    "CAD": ("Canadian dollar", "CA$"),
    "AUD": ("Australian dollar", "A$"),
    "SEK": ("Swedish krona", "SEK"),
    "NOK": ("Norwegian krone", "NOK"),
    "DKK": ("Danish krone", "DKK"),
    "PLN": ("Polish złoty", "zł"),
    "INR": ("Indian rupee", "₹"),
}

# ECB reference rates (units per 1 CHF) of 2026-11-20, used only when no live or saved rates are available.
BUILT_IN = {"date": "2026-11-20", "rates": {
    "EUR": 1.0842, "USD": 1.1916, "GBP": 0.90789, "JPY": 189.04, "CNY": 7.9571, "CAD": 1.7048,
    "AUD": 1.7876, "SEK": 12.1571, "NOK": 11.6209, "DKK": 8.1043, "PLN": 4.8339, "INR": 110.43}}

_LOCK = threading.Lock()
_STATE = {"rates": None, "next_fetch": None}


def _valid(rates):
    return all(isinstance(rates.get(code), (int, float)) and not isinstance(rates.get(code), bool)
               and math.isfinite(rates[code]) and rates[code] > 0 for code in CURRENCIES if code != "CHF")


def fetch_rates():
    """Today's ECB reference rates per CHF; raises OSError or ValueError when unavailable."""
    request = Request(RATES_URL, headers={"Accept": "application/json", "User-Agent": "financial-twin"})
    with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read())
    rates = data.get("rates") if isinstance(data, dict) else None
    if data.get("base") != "CHF" or not isinstance(rates, dict) or not _valid(rates):
        raise ValueError("unexpected exchange-rate answer")
    return {"date": str(data.get("date")), "rates": {code: float(rates[code]) for code in CURRENCIES if code != "CHF"}}


def _saved_rates():
    if RATES_FILE is None:
        return None
    try:
        saved = json.loads(Path(RATES_FILE).read_text(encoding="utf-8"))
        return saved if isinstance(saved.get("rates"), dict) and _valid(saved["rates"]) else None
    except (OSError, ValueError, AttributeError):
        return None


def _save(rates):
    if RATES_FILE is None:
        return
    path = Path(RATES_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rates), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def current_rates():
    """{"date", "rates", "source", "live"}: live rates when possible, else saved, else built in."""
    with _LOCK:
        if _STATE["next_fetch"] is None or monotonic() >= _STATE["next_fetch"]:
            try:
                rates = fetch_rates()
                _STATE["rates"] = {**rates, "source": "European Central Bank reference rate", "live": True}
                _save(rates)
            except (OSError, URLError, ValueError, TypeError, AttributeError):
                if _STATE["rates"] is None or not _STATE["rates"]["live"]:
                    saved = _saved_rates()
                    if saved:
                        _STATE["rates"] = {"date": saved["date"], "rates": saved["rates"],
                                           "source": "European Central Bank reference rate (saved, offline)", "live": False}
                    else:
                        _STATE["rates"] = {**BUILT_IN, "source": "European Central Bank reference rate (built in, offline)", "live": False}
            # A failed fetch is not repeated on every request, but sooner than a successful one.
            _STATE["next_fetch"] = monotonic() + (REFRESH_SECONDS if _STATE["rates"]["live"] else RETRY_SECONDS)
        return _STATE["rates"]


def reset():
    """Forget the cached rates (used by tests)."""
    with _LOCK:
        _STATE["rates"] = None
        _STATE["next_fetch"] = None


def currency_list():
    rates = current_rates()
    return {
        "base": "CHF", "date": rates["date"], "source": rates["source"], "live": rates["live"],
        "currencies": [{"code": code, "name": name, "symbol": symbol,
                        "per_chf": 1.0 if code == "CHF" else rates["rates"][code]}
                       for code, (name, symbol) in CURRENCIES.items()],
    }


def context(code):
    """The rate used for one request: {"code", "per_chf", "date", "source"}. Raises ValueError if unsupported."""
    code = "CHF" if code is None else code
    if not isinstance(code, str) or code.upper() not in CURRENCIES:
        raise ValueError(f"Unsupported currency. Choose one of: {', '.join(CURRENCIES)}.")
    code = code.upper()
    if code == "CHF":
        return {"code": "CHF", "per_chf": 1.0, "date": None, "source": None}
    rates = current_rates()
    return {"code": code, "per_chf": rates["rates"][code], "date": rates["date"], "source": rates["source"]}


# --- Amounts written in the story ---------------------------------------------------------------------------

# Words and symbols naming a currency. "$", "¥" and "kr" are shared, so they mean the chosen currency when it
# uses them, and otherwise USD, JPY or nothing (left to the AI with the instruction below).
_NAMES = {
    "CHF": r"CHF|SFr\.?|Fr\.|(?:Swiss\s+)?francs?",
    "EUR": r"EUR|€|euros?",
    "USD": r"USD|US\$|dollars?",
    "GBP": r"GBP|£|pounds?",
    "JPY": r"JPY|yen",
    "CNY": r"CNY|RMB|yuan",
    "CAD": r"CAD|CA\$|C\$",
    "AUD": r"AUD|A\$|AU\$",
    "SEK": r"SEK",
    "NOK": r"NOK",
    "DKK": r"DKK",
    "PLN": r"PLN|zł|zloty",
    "INR": r"INR|₹|rupees?",
}
_SHARED = {"$": ("USD", {"USD", "CAD", "AUD"}), "¥": ("JPY", {"JPY", "CNY"}), "kr": (None, {"SEK", "NOK", "DKK"})}

_NUMBER = r"\d{1,3}(?:[,'’]\d{3})+(?:\.\d+)?|\d{1,3}(?:\.\d{3})+(?!\d|\.\d)|\d+(?:\.\d+)?"
_SCALE = r"(?:\s?(?:k|K|m|M|mio|million|millions|bn|billion)\b)?"
_TOKEN = "|".join([*(f"(?:{pattern})" for pattern in _NAMES.values()), r"\$", "¥", r"kr\.?"])
_AMOUNT = re.compile(
    rf"(?<![A-Za-z])(?P<pre>{_TOKEN})\s?(?P<n1>{_NUMBER})(?P<s1>{_SCALE})"
    rf"|(?P<n2>{_NUMBER})(?P<s2>{_SCALE})\s?(?P<post>{_TOKEN})(?![A-Za-z])",
    re.IGNORECASE)


def _currency_of(token, chosen):
    token = token.strip()
    for shared, (fallback, users) in _SHARED.items():
        if token.lower().rstrip(".") == shared:
            return chosen if chosen in users else fallback
    return next((code for code, pattern in _NAMES.items() if re.fullmatch(pattern, token, re.IGNORECASE)), None)


def _number(text, scale):
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):  # 2.000 or 1.250.000: dots as thousands separators
        text = text.replace(".", "")
    value = float(re.sub(r"[,'’]", "", text))
    scale = scale.strip().lower()
    if scale == "k":
        value *= 1_000
    elif scale in ("m", "mio", "million", "millions"):
        value *= 1_000_000
    elif scale in ("bn", "billion"):
        value *= 1_000_000_000
    return value


def convert_text(text, chosen, rates):
    """Rewrite amounts written in a foreign currency as CHF, keeping the original next to it,
    for example "€2,000" -> "CHF 1,844.68 (written as €2,000)". CHF amounts and bare numbers are left alone.
    `rates` is a function returning units per CHF, called only if something needs converting.
    Returns (text, conversions) where conversions lists what was changed."""
    conversions = []

    def replace(match):
        token = match.group("pre") or match.group("post")
        code = _currency_of(token, chosen)
        if code is None or code == "CHF":
            return match.group(0)
        number = match.group("n1") or match.group("n2")
        scale = match.group("s1") if match.group("pre") else match.group("s2")
        amount = _number(number, scale or "")
        chf = round(amount / rates()[code], 2)
        conversions.append({"written": match.group(0), "currency": code, "amount": amount, "chf": chf})
        return f"CHF {chf:,.2f} (written as {match.group(0)})"

    return _AMOUNT.sub(replace, text), conversions


def ai_instruction(ctx):
    """Prompt text telling the AI how to treat amounts that have no currency, or "" for CHF."""
    if ctx["code"] == "CHF":
        return ""
    return (f" The user chose {ctx['code']} as their currency. Amounts already written in CHF are CHF. "
            f"Any amount written without a currency is in {ctx['code']}: convert it to CHF at exactly "
            f"1 {ctx['code']} = {1 / ctx['per_chf']:.6f} CHF ({ctx['source']} of {ctx['date']}). "
            "Return every amount in CHF.")


def prepare_for_ai(text, code):
    """The text to send to the AI with foreign amounts converted to CHF, the prompt instruction,
    the rate context used, and the list of conversions made."""
    ctx = context(code)
    converted, conversions = convert_text(text, ctx["code"], lambda: current_rates()["rates"])
    if conversions and ctx["code"] == "CHF":
        # CHF chosen, but the story names another currency: record which rates converted it.
        live = current_rates()
        ctx = {**ctx, "date": live["date"], "source": live["source"]}
    return converted, ai_instruction(ctx), ctx, conversions
