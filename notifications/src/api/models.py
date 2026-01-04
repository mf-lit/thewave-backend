"""Request and response models for the API."""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CreateNotificationRequest:
    """Request model for creating a notification."""

    performance_ak: str
    date: str
    time: str
    side: str
    notification_type: str
    thresholds: Optional[List[int]] = None

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate the request data.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Validate performance_ak is provided
        if not self.performance_ak or not isinstance(self.performance_ak, str):
            return False, "performance_ak is required and must be a string"

        # Validate date format (YYYY-MM-DD)
        try:
            from datetime import datetime

            datetime.strptime(self.date, "%Y-%m-%d")
        except ValueError:
            return False, "Invalid date format. Expected YYYY-MM-DD"

        # Validate time format (accepts HH:MM, HH:MM:SS, or HH:MM:SS.mmm)
        try:
            time_parts = self.time.split(":")
            if len(time_parts) < 2:
                raise ValueError
            hour = int(time_parts[0])
            minute_str = time_parts[1]
            # Handle milliseconds if present (e.g., "00.000")
            if "." in minute_str:
                minute_str = minute_str.split(".")[0]
            minute = int(minute_str)
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
            # Normalize time to HH:MM format for storage
            self.time = f"{hour:02d}:{minute:02d}"
        except (ValueError, IndexError):
            return False, "Invalid time format. Expected HH:MM, HH:MM:SS, or HH:MM:SS.mmm"

        # Validate side
        if self.side not in ["left", "right", "none"]:
            return False, "Invalid side. Must be 'left', 'right', or 'none'"

        # Validate notification_type
        if self.notification_type not in ["below_threshold", "above_zero"]:
            return (
                False,
                "Invalid notification_type. Must be 'below_threshold' or 'above_zero'",
            )

        # Validate thresholds for below_threshold type
        if self.notification_type == "below_threshold":
            if not self.thresholds or len(self.thresholds) == 0:
                return False, "thresholds is required for below_threshold notification_type"
            if not all(isinstance(t, int) and t >= 0 for t in self.thresholds):
                return False, "All thresholds must be non-negative integers"

        return True, None


@dataclass
class NotificationResponse:
    """Response model for a notification."""

    notification_id: str
    client_id: str
    performance_ak: str
    date: str
    time: str
    side: str
    title: str
    notification_type: str
    thresholds: Optional[List[int]] = None
    last_checked_availability: Optional[int] = None
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "NotificationResponse":
        """Create a NotificationResponse from a dictionary."""
        return cls(
            notification_id=data.get("notification_id", ""),
            client_id=data.get("client_id", ""),
            performance_ak=data.get("performance_ak", ""),
            date=data.get("date", ""),
            time=data.get("time", ""),
            side=data.get("side", ""),
            title=data.get("title", ""),
            notification_type=data.get("notification_type", ""),
            thresholds=data.get("thresholds"),
            last_checked_availability=data.get("last_checked_availability"),
            created_at=data.get("created_at", ""),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        result = {
            "notification_id": self.notification_id,
            "client_id": self.client_id,
            "performance_ak": self.performance_ak,
            "date": self.date,
            "time": self.time,
            "side": self.side,
            "title": self.title,
            "notification_type": self.notification_type,
            "created_at": self.created_at,
        }
        if self.thresholds is not None:
            result["thresholds"] = self.thresholds
        if self.last_checked_availability is not None:
            result["last_checked_availability"] = self.last_checked_availability
        return result

