import csv
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
sys.path.insert(0, str(AGENT))

BACKEND = AGENT.parent / "ReelScraper"
STUDIO = BACKEND / "studio" / "instagram"
ANALYSIS = BACKEND / "analysis" / "instagram"
CORPUS_CSV = BACKEND / "platforms" / "instagram" / "virality_reels.csv"


@pytest.fixture
def studio_md():
    """Read a real approved recipe. These tests deliberately parse the producer's actual
    output rather than a hand-written sample, so format drift surfaces here."""
    def _read(name):
        p = STUDIO / name
        if not p.exists():
            pytest.skip(f"studio fixture not present: {p}")
        return p.read_text(encoding="utf-8")
    return _read


@pytest.fixture(scope="session")
def studio_files():
    """Every recipe markdown in the studio, as (filename, text), fewest `### Shot` headings
    first. Selected by SHAPE, never by filename: a studio filename embeds a content_id
    fragment and a slug derived from a real creator's caption, so neither belongs in a
    committed test. Empty when the studio directory is absent."""
    if not STUDIO.is_dir():
        return []
    out = []
    for p in sorted(STUDIO.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append((p.name, text, text.count("\n### Shot ")))
    out.sort(key=lambda t: t[2])
    return [(name, text) for name, text, _ in out]


@pytest.fixture
def corpus_rows():
    """The REAL corpus rows the hub's /api/corpus/{p}/top serves, read straight off the
    analyzer's CSV (core/corpus.py's adapter boundary). Every value is a string here, exactly
    as the hub returns it — which is precisely what the ease heuristic has to survive."""
    if not CORPUS_CSV.exists():
        pytest.skip(f"corpus fixture not present: {CORPUS_CSV}")
    with open(CORPUS_CSV, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("virality_score")]
    rows.sort(key=lambda r: -float(r["virality_score"]))
    return rows


@pytest.fixture
def blueprint():
    """A real schema-2 blueprint by content_id, as written by the AnalysisEngine."""
    def _read(content_id):
        p = ANALYSIS / f"{content_id}.json"
        if not p.exists():
            pytest.skip(f"blueprint fixture not present: {p}")
        return json.loads(p.read_text(encoding="utf-8"))
    return _read


def _load_blueprints():
    """Every schema-2 blueprint on disk, cheapest-to-remake first (fewest shots, then
    shortest). Empty when the analysis directory is absent."""
    if not ANALYSIS.is_dir():
        return []
    out = []
    for p in sorted(ANALYSIS.glob("*.json")):
        try:
            bp = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue                       # a half-written blueprint is not a test failure
        if isinstance(bp, dict) and bp.get("shots") and bp.get("content_id"):
            out.append(bp)
    out.sort(key=lambda b: (len(b["shots"]), _duration(b)))
    return out


def _duration(bp):
    return sum(float(s.get("duration") or 0) for s in bp.get("shots", []))


@pytest.fixture(scope="session")
def blueprints():
    """Blueprints selected by SHAPE rather than by hard-coded content_id.

    The ids are Instagram `<media_pk>_<user_pk>` keys — the trailing half identifies a real
    account — so they must not be committed. Selecting on shots/duration also makes these
    tests work against ANY dataset the operator has scraped, not just the one they were
    written against."""
    return _load_blueprints()


@pytest.fixture
def single_shot_blueprint(blueprints):
    """A one-shot clip — the easiest possible remake, and the only shape that can assert on
    the `single-shot` ease reason."""
    for bp in blueprints:
        if len(bp["shots"]) == 1:
            return bp
    pytest.skip(f"no single-shot blueprint present: {ANALYSIS}")


@pytest.fixture
def simplest_blueprint(blueprints):
    """The easiest clip to remake: fewest shots, shortest."""
    if not blueprints:
        pytest.skip(f"no blueprints present: {ANALYSIS}")
    return blueprints[0]


@pytest.fixture
def hardest_blueprint(blueprints):
    """The most involved clip: most shots. Must be a strictly different shape from
    `simplest_blueprint`, or the ordering assertions prove nothing."""
    if not blueprints:
        pytest.skip(f"no blueprints present: {ANALYSIS}")
    hardest = blueprints[-1]
    if len(hardest["shots"]) <= len(blueprints[0]["shots"]):
        pytest.skip("dataset has no multi-shot blueprint to contrast against")
    return hardest


@pytest.fixture
def middle_blueprint(blueprints, hardest_blueprint):
    """A second clip that is no harder than `hardest_blueprint` and distinct from it, so the
    three-way ordering assertion has a real middle term."""
    for bp in blueprints[1:]:
        if bp["content_id"] != hardest_blueprint["content_id"]:
            return bp
    pytest.skip(f"need >= 3 distinct blueprints: {ANALYSIS}")
