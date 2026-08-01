"""The rules that decide whether a user's phone buzzes."""
from __future__ import annotations

import pytest

from src.evaluator import evaluate, evaluate_any_quiet
from src.models import (
    ABOVE_ZERO,
    ANY_QUIET_SESSION,
    BELOW_THRESHOLD,
    QUIET_SESSION,
    Notification,
)


def notification(**overrides) -> Notification:
    base = dict(
        client_id="c",
        notification_id="n",
        performance_ak="TWB.EVN1.PRF1",
        date="2026-08-05",
        time="18:00",
        side="left",
        title="Advanced Surf",
        notification_type=BELOW_THRESHOLD,
        created_at="2026-07-30T12:00:00",
        thresholds=[5, 2],
        notified_thresholds=[],
    )
    base.update(overrides)
    return Notification(**base)


# -- below_threshold ----------------------------------------------------------

def test_no_notification_while_above_every_threshold():
    assert evaluate(notification(), 9).notify is False


def test_fires_when_availability_reaches_a_threshold():
    decision = evaluate(notification(), 5)
    assert decision.notify
    assert decision.threshold == 5
    assert decision.newly_crossed == (5,)
    assert decision.message == "Availability (5) has fallen below threshold (5)"


def test_a_single_drop_can_cross_several_thresholds():
    """Dropping 10 -> 0 notifies once and arms nothing for a repeat."""
    decision = evaluate(notification(), 0)
    assert decision.threshold == 5
    assert decision.newly_crossed == (5, 2)


def test_does_not_fire_again_for_an_already_notified_threshold():
    assert evaluate(notification(notified_thresholds=[5]), 5).notify is False


def test_still_fires_for_a_lower_threshold_not_yet_notified():
    decision = evaluate(notification(notified_thresholds=[5]), 1)
    assert decision.notify
    assert decision.threshold == 2
    assert decision.newly_crossed == (2,)


def test_threshold_order_follows_the_clients_list():
    decision = evaluate(notification(thresholds=[2, 5]), 0)
    assert decision.threshold == 2
    assert decision.newly_crossed == (2, 5)


def test_zero_is_a_valid_threshold():
    decision = evaluate(notification(thresholds=[0]), 0)
    assert decision.notify
    assert decision.threshold == 0


# -- above_zero ---------------------------------------------------------------

def above_zero(**overrides) -> Notification:
    return notification(
        notification_type=ABOVE_ZERO, thresholds=None, notified_thresholds=[], **overrides
    )


def test_above_zero_fires_on_the_transition_from_sold_out():
    decision = evaluate(above_zero(last_checked_availability=0), 3)
    assert decision.notify
    assert decision.threshold is None
    assert decision.message == "Availability (3) has increased above zero"


def test_above_zero_fires_on_a_first_ever_check_with_seats():
    assert evaluate(above_zero(last_checked_availability=None), 3).notify


def test_above_zero_does_not_repeat_while_seats_remain():
    assert evaluate(above_zero(last_checked_availability=3), 4).notify is False


def test_above_zero_silent_while_still_sold_out():
    assert evaluate(above_zero(last_checked_availability=0), 0).notify is False


def test_above_zero_rearms_after_selling_out_again():
    """0 -> 3 -> 0 -> 2 should notify twice, not once."""
    assert evaluate(above_zero(last_checked_availability=0), 3).notify
    assert evaluate(above_zero(last_checked_availability=3), 0).notify is False
    assert evaluate(above_zero(last_checked_availability=0), 2).notify


@pytest.mark.parametrize("notification_type", ["something_else", ""])
def test_unknown_type_never_notifies(notification_type):
    assert evaluate(notification(notification_type=notification_type), 0).notify is False


# -- quiet_session ------------------------------------------------------------

def quiet(**overrides):
    fields = dict(
        notification_type=QUIET_SESSION, thresholds=None, minimum_slots=12, time_before="24h"
    )
    fields.update(overrides)
    return notification(**fields)


def test_quiet_session_fires_when_enough_slots_remain():
    decision = evaluate(quiet(), 20)
    assert decision.notify
    assert decision.message == "Session is quiet: 20 slots remaining (minimum 12)"


def test_quiet_session_leaves_the_threshold_field_alone():
    """`threshold` means 'at or below', which is not what this type tests."""
    assert evaluate(quiet(), 20).threshold is None


def test_quiet_session_fires_at_exactly_the_minimum():
    assert evaluate(quiet(), 12).notify is True


def test_quiet_session_stays_silent_when_the_session_is_filling_up():
    assert evaluate(quiet(), 11).notify is False


def test_quiet_session_never_fires_twice():
    """A stored reading means the single check has already happened."""
    assert evaluate(quiet(last_checked_availability=20), 20).notify is False


def test_quiet_session_without_a_minimum_never_notifies():
    """A malformed row must return a decision, not raise into the check cycle."""
    assert evaluate(quiet(minimum_slots=None), 20).notify is False


def test_a_zero_minimum_fires_on_any_availability():
    assert evaluate(quiet(minimum_slots=0), 0).notify is True


# -- any_quiet_session --------------------------------------------------------

def rolling(**overrides) -> Notification:
    base = dict(
        notification_type=ANY_QUIET_SESSION,
        performance_ak="",
        date="",
        time="",
        thresholds=None,
        minimum_slots=8,
        time_before="24h",
    )
    base.update(overrides)
    return notification(**base)


def test_any_quiet_fires_at_exactly_the_minimum():
    decision = evaluate_any_quiet(rolling(), "P1", 8)
    assert decision.notify
    assert decision.message == "Session is quiet: 8 slots remaining (minimum 8)"


def test_any_quiet_stays_silent_below_the_minimum():
    assert evaluate_any_quiet(rolling(), "P1", 7).notify is False


def test_any_quiet_never_fires_twice_for_one_session():
    already = rolling(notified_performances={"P1": "2026-08-05"})
    assert evaluate_any_quiet(already, "P1", 20).notify is False


def test_any_quiet_still_fires_for_a_session_it_has_not_seen():
    already = rolling(notified_performances={"P1": "2026-08-05"})
    assert evaluate_any_quiet(already, "P2", 20).notify is True


def test_any_quiet_without_a_minimum_never_notifies():
    """A malformed row must return a decision, not raise into the check cycle."""
    assert evaluate_any_quiet(rolling(minimum_slots=None), "P1", 20).notify is False


def test_any_quiet_ignores_the_last_reading():
    """The opposite of quiet_session: the fired-flag is per session, not per row."""
    assert evaluate_any_quiet(rolling(last_checked_availability=20), "P1", 20).notify is True


def test_a_zero_minimum_fires_on_a_sold_out_session():
    assert evaluate_any_quiet(rolling(minimum_slots=0), "P1", 0).notify is True
