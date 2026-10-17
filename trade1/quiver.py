"""Quiver Quantitative API client and alternative-data bias scoring.

Docs: https://api.quiverquant.com/docs/
Auth is a bearer token from your Quiver account page.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.quiverquant.com/beta"


class QuiverError(RuntimeError):
    pass


class QuiverClient:
    def __init__(self, token: str, base_url: str = BASE_URL, timeout: float = 20.0,
                 session: requests.Session | None = None):
        if not token:
            raise QuiverError("QUIVER_API_TOKEN is empty")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })

    def _get(self, path: str, params: dict | None = None, retries: int = 3) -> list[dict[str, Any]]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == retries:
                    raise QuiverError(f"GET {url} failed: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 429 and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (401, 403):
                raise QuiverError(f"Quiver rejected the token ({resp.status_code}). "
                                  "Check QUIVER_API_TOKEN and your subscription tier.")
            if not resp.ok:
                raise QuiverError(f"GET {url} -> {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            return data if isinstance(data, list) else [data]
        return []

    # --- endpoints -------------------------------------------------------
    def congress_trading(self, ticker: str | None = None) -> list[dict]:
        if ticker:
            return self._get(f"historical/congresstrading/{ticker}")
        return self._get("live/congresstrading")

    def insider_trading(self, ticker: str | None = None) -> list[dict]:
        return self._get("live/insiders", params={"ticker": ticker} if ticker else None)

    def gov_contracts(self) -> list[dict]:
        return self._get("live/govcontractsall")

    def lobbying(self) -> list[dict]:
        return self._get("live/lobbying")

    def wallstreetbets(self) -> list[dict]:
        return self._get("live/wallstreetbets")


# --- scoring ---------------------------------------------------------------

def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _congress_direction(row: dict) -> int:
    tx = str(row.get("Transaction", "")).lower()
    if "purchase" in tx or tx == "buy":
        return 1
    if "sale" in tx or tx == "sell":
        return -1
    return 0


def _insider_direction(row: dict) -> int:
    code = str(row.get("TransactionCode", "")).upper()
    if code == "P":  # open-market purchase
        return 1
    if code == "S":  # open-market sale
        return -1
    return 0  # grants, option exercises, gifts etc. carry little signal


@dataclass
class BiasScore:
    ticker: str
    score: float  # -1 (strong sell pressure) .. +1 (strong buy pressure)
    buys: int
    sells: int

    @property
    def count(self) -> int:
        return self.buys + self.sells


def score_rows(rows: Iterable[dict], direction_fn, date_keys: tuple[str, ...],
               since: datetime, weight: float = 1.0) -> dict[str, list[float]]:
    """Return {ticker: [weighted buys, weighted sells]} for rows newer than `since`."""
    out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        ticker = str(row.get("Ticker") or "").upper().strip()
        if not ticker:
            continue
        dt = next((d for d in (_parse_date(row.get(k)) for k in date_keys) if d), None)
        if dt is None or dt < since:
            continue
        d = direction_fn(row)
        if d > 0:
            out[ticker][0] += weight
        elif d < 0:
            out[ticker][1] += weight
    return out


class QuiverBias:
    """Combines congress + insider flows into a per-ticker bias score."""

    def __init__(self, client: QuiverClient, lookback_days: int = 30,
                 congress_weight: float = 1.0, insider_weight: float = 1.0,
                 refresh_minutes: int = 60):
        self.client = client
        self.lookback_days = lookback_days
        self.congress_weight = congress_weight
        self.insider_weight = insider_weight
        self.refresh_seconds = refresh_minutes * 60
        self._scores: dict[str, BiasScore] = {}
        self._last_refresh = 0.0

    def refresh(self, force: bool = False) -> dict[str, BiasScore]:
        if not force and time.time() - self._last_refresh < self.refresh_seconds:
            return self._scores
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        sources = (
            (self.client.congress_trading, _congress_direction,
             ("TransactionDate", "ReportDate"), self.congress_weight),
            (self.client.insider_trading, _insider_direction, ("Date", "fileDate"), self.insider_weight),
        )
        for fetch, fn, keys, w in sources:
            if w <= 0:
                continue
            try:
                rows = fetch()
            except QuiverError as exc:
                log.warning("Quiver source %s failed: %s", fetch.__name__, exc)
                continue
            for t, (b, s) in score_rows(rows, fn, keys, since, w).items():
                totals[t][0] += b
                totals[t][1] += s
        self._scores = {
            t: BiasScore(t, (b - s) / (b + s) if b + s else 0.0, round(b), round(s))
            for t, (b, s) in totals.items()
        }
        self._last_refresh = time.time()
        log.info("Quiver bias refreshed: %d tickers", len(self._scores))
        return self._scores

    def get(self, ticker: str) -> BiasScore:
        return self._scores.get(ticker.upper(), BiasScore(ticker.upper(), 0.0, 0, 0))
