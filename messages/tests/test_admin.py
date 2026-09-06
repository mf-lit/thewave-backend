"""The recovery path.

The handlers are tested directly rather than through ``main``, which builds
its own ``Services`` from the environment. What matters here is that each
command does the right thing to the database and says so.
"""
from __future__ import annotations

import pytest

from src import admin
from src.models import MessageRequest

from conftest import make_payload

CLIENT_ID = "11111111-1111-1111-1111-111111111111"


def seed(services, **overrides):
    return services.messages.create(
        MessageRequest.from_payload(make_payload(**overrides))
    )


def run(services, argv):
    args = admin.build_parser().parse_args(argv)
    return args.handler(services, args)


def test_list_on_an_empty_database(services, capsys):
    assert run(services, ["list"]) == 0
    assert "No messages." in capsys.readouterr().out


def test_list_shows_the_ack_count(services, capsys):
    message = seed(services, title="Closure")
    services.messages.record_acks(CLIENT_ID, [(message.message_id, 1)])

    assert run(services, ["list"]) == 0
    out = capsys.readouterr().out
    assert "Closure" in out
    assert message.message_id in out


def test_show(services, capsys):
    message = seed(services)
    assert run(services, ["show", message.message_id]) == 0
    out = capsys.readouterr().out
    assert "Lagoon closed Tuesday" in out
    assert "revision" in out


def test_disable_and_enable(services, capsys):
    message = seed(services)

    assert run(services, ["disable", message.message_id]) == 0
    assert services.messages.get(message.message_id).enabled is False
    assert "now disabled" in capsys.readouterr().out

    assert run(services, ["enable", message.message_id]) == 0
    assert services.messages.get(message.message_id).enabled is True


def test_delete(services, capsys):
    message = seed(services)
    assert run(services, ["delete", message.message_id]) == 0
    assert services.messages.get(message.message_id) is None


@pytest.mark.parametrize("command", ["show", "enable", "disable", "delete", "audience"])
def test_an_unknown_id_exits_non_zero(services, capsys, command):
    with pytest.raises(SystemExit) as excinfo:
        run(services, [command, "no-such-message"])
    assert excinfo.value.code == 1
    assert "No message with id no-such-message." in capsys.readouterr().out


def test_audience_reports_when_the_directory_is_unavailable(services, capsys):
    """A count of nought and a count we cannot take are different answers."""
    message = seed(services)
    assert run(services, ["audience", message.message_id]) == 1
    assert "No client directory available" in capsys.readouterr().out
