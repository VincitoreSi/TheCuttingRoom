"""The URL the hub PRINTS is not always the address it BINDS.

Reported from a plain `docker run`: the hub logged `HUB_URL=http://0.0.0.0:8787`, and both
that link and `/docs` opened `about:blank` in the browser, while typing `localhost:8787` by
hand worked. Nothing was wrong with the server — the address it advertised was never a
destination.

`0.0.0.0` is a BIND instruction meaning "every interface on this host". It is not routable,
and Chrome refuses to navigate to it outright, which is why the failure looked like a dead
page rather than a connection error. In the container the wildcard bind is deliberate and
required (`docker/Dockerfile` sets `HUB_HOST=0.0.0.0`; it is the only address compose can
forward a published port to), so the bind is correct and only the advertisement was wrong.

The same string was also exported as `BACKEND_API` for every stage the hub spawns —
`docker/docker-compose.yml` has carried a commented-out `HUB_ADVERTISE` knob describing
exactly this ("stop BACKEND_API becoming http://0.0.0.0") since before it was implementable.

NOTE FOR ANYONE EDITING cli.py: `./health` greps `ReelScraper/cli.py` and `ReelScraper/api`
for a literal `0.0.0.0` and FAILS the "hub binds loopback only" invariant if it finds one
(health:304, RISKS.md R2). That is why the wildcard is detected through
`ipaddress.is_unspecified` rather than by comparing against the string — the semantic test is
both more correct (it catches `::` too) and the only one that does not trip the guard.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli import hub_url  # noqa: E402


def test_a_wildcard_bind_is_advertised_as_loopback(monkeypatch):
    """The reported bug. A browser cannot open the wildcard address."""
    monkeypatch.delenv("HUB_ADVERTISE", raising=False)
    assert hub_url("0.0.0.0", 8787) == "http://127.0.0.1:8787"


def test_the_ipv6_wildcard_gets_the_same_treatment(monkeypatch):
    """`::` is unspecified for exactly the same reason, and a URL host that is an IPv6
    literal has to be bracketed or it is not parseable at all."""
    monkeypatch.delenv("HUB_ADVERTISE", raising=False)
    assert hub_url("::", 8787) == "http://[::1]:8787"


def test_a_real_bind_address_is_left_alone(monkeypatch):
    """The host lane binds 127.0.0.1 and must be completely unaffected: this fix may only
    ever touch the case where the bind address is not a destination."""
    monkeypatch.delenv("HUB_ADVERTISE", raising=False)
    assert hub_url("127.0.0.1", 8787) == "http://127.0.0.1:8787"
    assert hub_url("192.168.1.50", 9000) == "http://192.168.1.50:9000"


def test_a_hostname_is_passed_through(monkeypatch):
    """Not every bind host parses as an IP. A name is already dialable, so it is returned
    verbatim rather than being second-guessed."""
    monkeypatch.delenv("HUB_ADVERTISE", raising=False)
    assert hub_url("localhost", 8787) == "http://localhost:8787"


def test_hub_advertise_overrides_everything(monkeypatch):
    """The knob docker-compose.yml has described for longer than it has existed. A hub
    published under a name or a LAN address it cannot infer from its own bind needs to be
    told, and the override must win even when the bind address IS dialable."""
    monkeypatch.setenv("HUB_ADVERTISE", "hub.local")
    assert hub_url("0.0.0.0", 8787) == "http://hub.local:8787"
    assert hub_url("127.0.0.1", 8787) == "http://hub.local:8787"


def test_a_blank_override_is_ignored_rather_than_obeyed(monkeypatch):
    """An unset variable in compose reaches the process as "" rather than as absent. Obeying
    it literally would advertise `http://:8787`, which is worse than what we started with."""
    monkeypatch.setenv("HUB_ADVERTISE", "   ")
    assert hub_url("0.0.0.0", 8787) == "http://127.0.0.1:8787"


@pytest.mark.parametrize("bind", ["0.0.0.0", "::", "127.0.0.1", "localhost"])
def test_the_advertised_url_is_never_the_wildcard(monkeypatch, bind):
    """The property the bug violated, stated directly: whatever we bind, what we PRINT and
    hand to sibling agents as BACKEND_API must be something a client can connect to."""
    monkeypatch.delenv("HUB_ADVERTISE", raising=False)
    url = hub_url(bind, 8787)
    assert "0.0.0.0" not in url
    assert "[::]" not in url
