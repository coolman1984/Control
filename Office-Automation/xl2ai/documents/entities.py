"""Deterministic entities in free text: the values that link a document to data.

Codes (INV-005000, C101), money with its currency, dates, percentages, e-mail addresses and phone numbers -- found by
rules, never guessed by a model, each with its exact character span so it can be shown in context. Arabic-Indic
digits are read as digits. Codes are what later link a document to the tables that contain the same value.
"""
from __future__ import annotations

import datetime as dt
import re

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
CURRENCIES = {"egp": "EGP", "le": "EGP", "جنيه": "EGP", "جنيها": "EGP", "جنيهات": "EGP", "ج.م": "EGP", "usd": "USD",
              "$": "USD", "دولار": "USD", "eur": "EUR", "€": "EUR", "يورو": "EUR", "sar": "SAR", "ريال": "SAR",
              "aed": "AED", "درهم": "AED"}
_NUM = r"\d{1,3}(?:[,٬]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_CUR = "|".join(sorted((re.escape(c) for c in CURRENCIES), key=len, reverse=True))

PATTERNS = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("money", re.compile(rf"(?:(?P<c1>{_CUR})\s?(?P<n1>{_NUM})|(?P<n2>{_NUM})\s?(?P<c2>{_CUR}))(?![\w])", re.I)),
    ("date", re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b|\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")),
    ("date", re.compile(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(\d{4})\b"
                        r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b"
                        r"|(\d{1,2})\s+(يناير|فبراير|مارس|أبريل|ابريل|مايو|يونيو|يوليو|أغسطس|اغسطس|سبتمبر|أكتوبر|اكتوبر"
                        r"|نوفمبر|ديسمبر)\s+(\d{4})", re.I)),
    ("percent", re.compile(rf"(?:{_NUM})\s?%")),
    ("phone", re.compile(r"(?<![\w.])(?:\+20\s?|0)1[0125]\d{8}(?!\d)|(?<![\w.])\+\d{1,3}[\s-]?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}")),
    ("code", re.compile(r"\b[A-Z]{1,6}[-_/]?\d{2,}(?:[-/]\d+)?\b")),
]


def normalize_digits(text):
    return (text or "").translate(_DIGITS)


MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                        "dec"), 1)}
MONTHS.update({"يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7,
               "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12})


def _date(m):
    g = m.groups()
    if len(g) == 9:                                   # the month-name pattern
        try:
            if g[0]:
                d, mo, y = int(g[0]), MONTHS[g[1].lower()[:3]], int(g[2])
            elif g[3]:
                mo, d, y = MONTHS[g[3].lower()[:3]], int(g[4]), int(g[5])
            else:
                d, mo, y = int(g[6]), MONTHS[g[7]], int(g[8])
            return dt.date(y, mo, d).isoformat()
        except (KeyError, ValueError):
            return None
    try:
        if m.group(1):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            d, mo, y = int(m.group(4)), int(m.group(5)), int(m.group(6))
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def find_entities(text):
    """[(kind, value as written, normalized, start, end)]; overlapping matches keep the first (more specific) kind."""
    text = normalize_digits(text)
    found, taken = [], []
    for kind, pat in PATTERNS:
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < te and e > ts for ts, te in taken):
                continue
            raw = m.group(0)
            if kind == "money":
                num = (m.group("n1") or m.group("n2")).replace(",", "").replace("٬", "")
                cur = CURRENCIES.get((m.group("c1") or m.group("c2")).lower())
                norm = f"{float(num):.2f} {cur}"
            elif kind == "date":
                norm = _date(m)
                if norm is None:
                    continue
            elif kind == "percent":
                norm = raw.replace(" ", "")
            elif kind == "email":
                norm = raw.lower()
            elif kind == "phone":
                norm = re.sub(r"[\s-]", "", raw)
            else:
                norm = raw.upper()
            found.append((kind, raw, norm, s, e))
            taken.append((s, e))
    return sorted(found, key=lambda x: x[3])
