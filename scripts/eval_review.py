"""Does the reviewer pay the right agents? Measures a reviewer on evals/review_cases.json.

    python3 -m scripts.eval_review           # Claude + rule checks if ANTHROPIC_API_KEY is set, else rules
    python3 -m scripts.eval_review --rules   # rule checks only (no AI, free, deterministic)
    python3 -m scripts.eval_review --json evals/results.json

Only an exact "pass" pays, as in the market, so "hold" counts as not paid. The
number that matters most is junk paid: work that should fail but would have been
paid (money lost). Exit code 1 if any junk was paid. A Claude run costs one API
call per case the rule checks let through.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guild.brain import default_brain  # noqa: E402
from guild.lead import DEMO_JOBS  # noqa: E402
from guild.review import RuleReviewer  # noqa: E402

CASES = ROOT / "evals" / "review_cases.json"
JOBS = {job.id: job for job, _ in DEMO_JOBS}
KINDS = ("good", "junk", "subtle", "injection")


def load_cases(path: Path = CASES) -> list:
    return json.loads(path.read_text())


def evaluate(reviewer, cases: list) -> dict:
    rows = []
    for case in cases:
        job = JOBS[case["job_id"]]
        try:
            verdict = reviewer.review(job, {"items": case["items"]}).get("verdict")
        except Exception as exc:  # a failing reviewer holds, like in production
            verdict = f"hold ({type(exc).__name__})"
        verdict = str(verdict)
        paid = verdict == "pass"
        rows.append({"id": case["id"], "kind": case["kind"], "expected": case["expected"], "got": verdict,
                     "paid": paid, "correct": paid == (case["expected"] == "pass")})
    by_kind = defaultdict(lambda: [0, 0])
    for r in rows:
        by_kind[r["kind"]][0] += r["correct"]
        by_kind[r["kind"]][1] += 1
    good = [r for r in rows if r["expected"] == "pass"]
    bad = [r for r in rows if r["expected"] == "fail"]
    return {
        "reviewer": getattr(reviewer, "label", type(reviewer).__name__),
        "cases": len(rows),
        "accuracy": sum(r["correct"] for r in rows) / len(rows) if rows else 0.0,
        "good_paid": sum(r["paid"] for r in good), "good_total": len(good),
        "junk_paid": sum(r["paid"] for r in bad), "junk_total": len(bad),
        "by_kind": {k: {"correct": c, "total": t} for k, (c, t) in by_kind.items()},
        "verdicts": dict(Counter(r["got"].split(" ")[0] for r in rows)),
        "rows": rows,
    }


def report(result: dict) -> str:
    lines = [f"reviewer: {result['reviewer']}   cases: {result['cases']}   accuracy: {result['accuracy']:.0%}",
             f"good work paid:  {result['good_paid']}/{result['good_total']}",
             f"bad work paid:   {result['junk_paid']}/{result['junk_total']}   <- money lost",
             "by kind:"]
    for k in KINDS:
        if k in result["by_kind"]:
            c, t = result["by_kind"][k]["correct"], result["by_kind"][k]["total"]
            lines.append(f"  {k:<10} {c}/{t} correct")
    for r in result["rows"]:
        if not r["correct"]:
            lines.append(f"  MISS {r['id']} ({r['kind']}): expected {r['expected']}, got {r['got']}")
    return "\n".join(lines)


def main(argv: list) -> int:
    reviewer = RuleReviewer() if "--rules" in argv else default_brain()[1]
    result = evaluate(reviewer, load_cases())
    print(report(result))
    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        out.write_text(json.dumps(result, indent=1) + "\n")
        print(f"wrote {out}")
    return 1 if result["junk_paid"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
