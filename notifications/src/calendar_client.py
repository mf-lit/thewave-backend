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

from .models import ValidationError, normalize_time, normalize_title

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30

# The widest window upstream-api will serve in one call (its MAX_NUMBER_OF_DAYS).
MAX_CALENDAR_DAYS = 7
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


@dataclass(frozen=True)
class Session:
    """One performance seen from the point of view of one side."""

    date: str  # YYYY-MM-DD, Europe/London
    time: str  # canonical HH:MM
    performance_ak: str
    title: str  # upstream's own casing, for display
    availability: int  # seats on the side that was asked for


def matching_sessions(data: CalendarData, title: str, side: str) -> List[Session]:
    """Every performance in ``data`` whose title and side match, soonest first.

    The counterpart to `find_performance` for watches that name a kind of
    session rather than one session. Titles are compared with
    `models.normalize_title`, which folds case and nothing else, so
    "Advanced Surf" never matches "Advanced Surf Lesson".

    ``date`` comes from the day the performance was returned under rather than
    ``performance["date"]``: that is the key `fetch_dates` filters on, and it
    is present in every payload shape this service has seen.
    """
    wanted = normalize_title(title)
    if not wanted:
        return []

    sessions: List[Session] = []
    for day in data.days:
        date = day.get("date")
        if not date:
            continue
        for performance in day.get("performances", []):
            upstream_title = performance_title(performance)
            if normalize_title(upstream_title) != wanted:
                continue
            performance_ak = performance.get("performanceAK")
            availability = availability_for_side(performance, side)
            if not performance_ak or availability is None:
                continue
            try:
                start = normalize_time(performance.get("time"))
            except ValidationError:
                logger.warning(
                    "Skipping performance %s: unreadable time %r",
                    performance_ak,
                    performance.get("time"),
                )
                continue
            sessions.append(
                Session(
                    date=date,
                    time=start,
                    performance_ak=performance_ak,
                    title=upstream_title,
                    availability=availability,
                )
            )
    return sorted(sessions, key=lambda s: (s.date, s.time))


def availability_for_side(performance: Dict, side: str) -> Optional[int]:
    """Seats available on ``side``, or None if the side isn't sold here."""
    for product in performance.get("availabilityPerProduct", []):
        if product.get("side") == side:
            return product.get("availability", {}).get("available")
    return None


def performance_title(performance: Dict) -> str:
    return performance.get("fields", {}).get("title", "")


def group_consecutive(dates: Sequence[str]) -> List[List[str]]:
    """Split sorted dates into runs of consecutive calendar days.

    Runs are capped at ``MAX_CALENDAR_DAYS``: upstream-api rejects a wider
    window outright, so a long run has to go out as several calls rather than
    one that fails. Rolling watches never reach the cap (48h is three days at
    most), but per-session rows spread across a fortnight would.
    """
    if not dates:
        return []
    groups = [[dates[0]]]
    for date in dates[1:]:
        previous = datetime.strptime(groups[-1][-1], DATE_FORMAT)
        current = datetime.strptime(date, DATE_FORMAT)
        if (current - previous).days == 1 and len(groups[-1]) < MAX_CALENDAR_DAYS:
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
