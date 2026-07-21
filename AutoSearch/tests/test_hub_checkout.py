"""foreign_checkout — the one place a cross-clone mix-up is detectable.

Two clones, one niche each, is the supported way to run two niches at once. They share
nothing but the loopback, and BACKEND_API is the only thing aiming this agent at a hub. A
.env copied between clones, or a stale `export BACKEND_API=` in a shell, points it at the
other one — and then every call succeeds: work is read from that niche's corpus and written
to that niche's studio, under this agent's name. Nothing else in the system can notice.

Fail-closed on a definite mismatch, fail-OPEN on silence: a hub too old to serve /api/hub,
or one that is simply unreachable, is not evidence of a mismatch.
"""
from pathlib import Path

from engine.hub import HubClient, HubError

REPO = Path(__file__).resolve().parents[2]
OURS = str(REPO / "ReelScraper")


def _hub(reply):
    """A client whose /api/hub call returns `reply` (or raises it)."""
    c = HubClient("http://127.0.0.1:8787")

    def fake(method, path, **kw):
        if isinstance(reply, Exception):
            raise reply
        return reply

    c._request = fake
    return c


def test_silent_when_the_hub_is_our_own():
    assert _hub({"root": OURS}).foreign_checkout() is None


def test_names_the_other_checkout():
    other = "/Users/somebody/fitness-clone/ReelScraper"
    assert _hub({"root": other}).foreign_checkout() == other


def test_silent_when_the_hub_is_too_old_to_answer():
    """A hub predating /api/hub 404s. Refusing to run then would break every older setup."""
    assert _hub(HubError("GET /api/hub -> 404: Not Found")).foreign_checkout() is None


def test_silent_when_the_hub_is_unreachable():
    assert _hub(HubError("transport error")).foreign_checkout() is None


def test_silent_when_the_field_is_absent_or_empty():
    assert _hub({}).foreign_checkout() is None
    assert _hub({"root": ""}).foreign_checkout() is None
    assert _hub(None).foreign_checkout() is None


def test_paths_are_compared_resolved_not_as_strings():
    """Same directory, different spelling, must not read as a foreign checkout."""
    noisy = str(REPO / "ReelScraper" / ".." / "ReelScraper")
    assert noisy != OURS
    assert _hub({"root": noisy}).foreign_checkout() is None
