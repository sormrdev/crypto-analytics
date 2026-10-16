import logging
import time
from datetime import datetime
from typing import Any

import httpx

from crypto_analytics.config import Settings
from crypto_analytics.domain import Candle, Timeframe, Trade

from . import parsing

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRYABLE_ERRORS = ("EAPI:Rate limit", "EGeneral:Too many requests", "EService:Unavailable", "EService:Busy")


class KrakenAPIError(RuntimeError):
    """Kraken answered, but reported an error in the `error` field."""


class KrakenClient:
    """Thin client for Kraken's public REST API with throttling and retries."""

    def __init__(self, settings: Settings, http: httpx.Client | None = None, sleep=time.sleep) -> None:
        self._settings = settings
        self._http = http or httpx.Client(base_url=settings.kraken_base_url, timeout=settings.http_timeout_s)
        self._sleep = sleep
        self._last_call = 0.0

    def __enter__(self) -> "KrakenClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    # --- endpoints -------------------------------------------------------------

    def usd_pairs(self) -> list[str]:
        return parsing.parse_usd_pairs(self._get("/AssetPairs"), self._settings.quote_asset)

    def ohlc(self, pair: str, timeframe: Timeframe, since: datetime | None = None) -> list[Candle]:
        """Up to the 720 most recent candles. The last one is still forming."""
        params: dict[str, Any] = {"pair": pair, "interval": timeframe.kraken_interval}
        if since is not None:
            params["since"] = int(since.timestamp())
        return parsing.parse_ohlc(self._get("/OHLC", params))

    def trades(self, pair: str, since: str) -> tuple[list[Trade], str]:
        """One page (max 1000) of trades after the `since` cursor, plus the cursor for the next page."""
        result = self._get("/Trades", {"pair": pair, "since": since, "count": 1000})
        return parsing.parse_trades(result), str(result["last"])

    # --- transport -------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(1, self._settings.max_retries + 1):
            self._throttle()
            try:
                response = self._http.get(path, params=params)
                if response.status_code in RETRYABLE_STATUS:
                    raise _Retryable(f"HTTP {response.status_code}")
                response.raise_for_status()
                payload = response.json()
                errors = payload.get("error") or []
                if any(e.startswith(RETRYABLE_ERRORS) for e in errors):
                    raise _Retryable(", ".join(errors))
                if errors:
                    raise KrakenAPIError(f"{path} {params}: {', '.join(errors)}")
                return payload["result"]
            except (_Retryable, httpx.TransportError) as exc:
                if attempt == self._settings.max_retries:
                    raise KrakenAPIError(f"{path} failed after {attempt} attempts: {exc}") from exc
                backoff = min(2**attempt, 60)
                log.warning("Kraken %s: %s - retrying in %ss (%s/%s)", path, exc, backoff, attempt,
                            self._settings.max_retries)
                self._sleep(backoff)
        raise AssertionError("unreachable")

    def _throttle(self) -> None:
        wait = self._settings.request_interval_s - (time.monotonic() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.monotonic()


class _Retryable(Exception):
    pass
