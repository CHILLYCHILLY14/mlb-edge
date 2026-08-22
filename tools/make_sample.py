"""Generate a fully synthetic sample feed so the dashboard can be previewed
before the first live run. Never used in production."""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import fake_api
from pipeline.sources import mlb_api, espn, weather
mlb_api.get_json = fake_api.responder
espn.get_json = fake_api.responder
weather.get_json = fake_api.responder

from pipeline import build as B, grade as G, config as C

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/data"
    past = ["2026-08-18", "2026-08-19", "2026-08-20"]
    for d in past:
        B.main(["--date", d, "--days", "1", "--out", out, "--no-grade"])
        fake_api.FINAL_DATES.add(d)
    G.grade_all()
    B.main(["--date", "2026-08-21", "--days", "2", "--out", out])
    print("sample feed written to", out)
