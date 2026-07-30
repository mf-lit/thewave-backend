"""Client for the upstream calendar API.

Availability lives behind ``GET <base>?dateFrom=&numberOfDays=``. Runs of
consecutive dates are collapsed into a single request, which is what keeps
the per-cycle upstream call count down when several notifications watch
neighbouring days.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
DATE_FORMAT = "%Y-%m-%d"


class CalendarError(RuntimeError):
    """The calendar API could not be reached or returned an error."""


@dataclass(frozen=True)
class CalendarData:
    """A calendar response, indexed for lookup by performance key."""

    days: List[Dict]

    def find_performance(self, performance_ak: str) -> Optional[Dict]:
        for day in self.days:
            for performance in day.get("performances", []):
                if performance.get("performanceAK") == performance_ak:
                    return performance
        return None


def availability_for_side(performance: Dict, side: str) -> Optional[int]:
    """Seats available on ``side``, or None if the side isn't sold here."""
    for product in performance.get("availabilityPerProduct", []):
        if product.get("side") == side:
            return product.get("availability", {}).get("available")
    return None


def performance_title(performance: Dict) -> str:
    return performance.get("fields", {}).get("title", "")


def group_consecutive(dates: Sequence[str]) -> List[List[str]]:
    """Split sorted dates into runs of consecutive calendar days."""
    if not dates:
        return []
    groups = [[dates[0]]]
    for date in dates[1:]:
        previous = datetime.strptime(groups[-1][-1], DATE_FORMAT)
        current = datetime.strptime(date, DATE_FORMAT)
        if (current - previous).days == 1:
            groups[-1].append(date)
        else:
            groups.append([date])
    return groups


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.3,
        status_forcelist=(502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


class CalendarClient:
    def __init__(
        self,
        base_url: str,
        api_key: Optional[Callable[[], Optional[str]]] = None,
        timeout: int = REQUEST_TIMEOUT,
        session: Optional[requests.Session] = None,
    ):
        """``api_key`` is a callable so a rotated config file is picked up
        without restarting the process."""
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or _build_session()

    def _headers(self) -> Dict[str, str]:
        key = self.api_key() if self.api_key else None
        return {"x-api-key": key} if key else {}

    def _get(self, date_from: str, number_of_days: int) -> Dict:
        try:
            response = self.session.get(
                self.base_url,
                params={"dateFrom": date_from, "numberOfDays": number_of_days},
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CalendarError(
                f"calendar request failed for {date_from}+{number_of_days}d: {exc}"
            ) from exc

    def fetch_day(self, date: str) -> CalendarData:
        """Fetch a single day — used when validating a new notification."""
        return CalendarData(days=self._get(date, 1).get("days", []))

    def fetch_dates(self, dates: Sequence[str]) -> CalendarData:
        """Fetch exactly ``dates``, batching consecutive runs into one call."""
        wanted = sorted(set(dates))
        if not wanted:
            return CalendarData(days=[])

        selected: List[Dict] = []
        for group in group_consecutive(wanted):
            payload = self._get(group[0], len(group))
            selected.extend(
                day for day in payload.get("days", []) if day.get("date") in set(wanted)
            )
        return CalendarData(days=selected)
