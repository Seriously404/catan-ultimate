import pytest


@pytest.fixture(autouse=True)
def no_presentation_delay_in_rule_tests(monkeypatch):
    # Rule tests issue commands immediately. Pacing tests explicitly opt into a delay.
    monkeypatch.setenv("CATAN_ACTION_DELAY", "0")
