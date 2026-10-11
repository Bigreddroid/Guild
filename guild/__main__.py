"""Run the demo goal in the terminal: python3 -m guild"""
from decimal import Decimal

from guild.agents import default_agents
from guild.brain import default_brain
from guild.lead import DEMO_GOAL
from guild.run import run


def narrate(e):
    t = e["type"]
    if t == "plan":
        print(f"GOAL  {e['goal']} (agent budget: see DONE line)\n      lead: {e['lead']} · reviewer: {e['reviewer']}")
    elif t == "bids":
        top = ", ".join(f"{b['agent_id']} ${b['price']} {b['stars']}★" for b in e["bids"] if b["eligible"])
        print(f"\n[{e['job_id']}] bids (attempt {e['attempt']}): {top}")
    elif t == "awarded":
        print(f"  → awarded to {e['agent_id']} at ${e['price']}")
    elif t == "work":
        print(f"  … {e['agent_id']} worked via {e['tool']} ({e['mode']})")
    elif t == "settled":
        print(f"  ✓ review {e['verdict']}: {'; '.join(e['reasons'])} → {e['outcome'].upper()}"
              f" · {e['agent_id']} now {e['stars_after']}★ · spent ${e['spent']}")
    elif t in ("unfunded", "blocked"):
        print(f"  ! {t}: {e}")
    elif t == "done":
        print(f"\nDONE  spent ${e['spent']} of ${e['budget']} · pay owners: {e['owed']}")


if __name__ == "__main__":
    lead, reviewer = default_brain()
    run(DEMO_GOAL, Decimal("3.00"), default_agents(), lead, reviewer, narrate)
