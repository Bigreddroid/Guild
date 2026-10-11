"""Run a demo in the terminal.

    python3 -m guild          a deal bounty: agents hunt, Guild verifies, the best real deal wins
    python3 -m guild market   the earlier prototype: an agent job market (offsite demo)
"""
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


def narrate_hunt(e):
    t = e["type"]
    if t == "want":
        print(f"WANT  {e['title']}  (bounty ${e['bounty']}, deadline {e['deadline_s']:.0f}s)")
        for a in e["agents"]:
            print(f"      {a['name']:<10} {a['tool']:<18} {a['mode']:<9} {a['stars']}★  up to {a['max_claims']} claims")
    elif t == "claim":
        mark = "✓" if e["valid"] else "✗"
        print(f"  {mark} {e.get('agent_name', e['agent_id']):<10} claimed ${e['claimed']:<8} {e['url'][:60]}")
        print(f"      page: {('$' + e['page_price']) if e['page_price'] else '-':<9} {'; '.join(e['reasons'])}  ->  {e['stars_after']}★")
    elif t == "winner":
        print(f"\nWINNER  {e.get('agent_name', e['agent_id'])} found it for ${e['price']} (saves ${e['saved']}); bounty ${e['bounty']} to its developer")
    elif t == "no_winner":
        print(f"\nNO WINNER  {e['reason']}; the bounty is not charged")
    elif t == "agent_error":
        print(f"  ! {e['agent_id']} failed: {e['error']}")


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] != ["market"]:
        from guild.fetcher import SafeFetcher
        from guild.hunt import run_hunt
        from guild.hunters import default_hunters
        from guild.shops import DEMO_WANT, serve
        run_hunt(DEMO_WANT, default_hunters(), SafeFetcher(local=serve), narrate_hunt, deadline_s=30, speed=0.3)
    else:
        lead, reviewer = default_brain()
        run(DEMO_GOAL, Decimal("3.00"), default_agents(), lead, reviewer, narrate)
