import datetime as dt

import templates
from config import settings


def test_every_template_has_required_fields():
    for t in templates.TEMPLATES:
        for field in ("{greeting}", "{sender}", "{offer}", "{phone}", "{opt_out}"):
            assert field in t


def test_render_fills_in_offer_phone_and_name():
    row = {"agent_name": "Jane Smith", "address": "123 Main St"}
    msg = templates.render(row, seed=1, now=dt.datetime(2026, 5, 28, 9, 0))
    assert "Jane" in msg                      # personalized greeting
    assert settings.contact_phone in msg      # real phone
    assert settings.special_offer in msg      # real offer
    assert settings.sender_first_name in msg  # sender name
    assert "{" not in msg                     # all placeholders filled


def test_time_aware_greeting():
    row = {"agent_name": "Bob Lee"}
    morning = templates.render(row, seed=0, now=dt.datetime(2026, 5, 28, 8, 0))
    afternoon = templates.render(row, seed=0, now=dt.datetime(2026, 5, 28, 14, 0))
    assert "Good morning" in morning
    assert "Good afternoon" in afternoon


def test_render_handles_missing_name():
    msg = templates.render({"address": "9 Oak Ave"}, seed=2, now=dt.datetime(2026, 5, 28, 8, 0))
    assert "Good morning!" in msg  # no trailing name when none known
    assert "{" not in msg


def test_opt_out_toggle(monkeypatch):
    row = {"agent_name": "Jane Smith"}
    monkeypatch.setattr(settings, "include_opt_out", True)
    assert "STOP" in templates.render(row, seed=1)
    monkeypatch.setattr(settings, "include_opt_out", False)
    assert "STOP" not in templates.render(row, seed=1)
