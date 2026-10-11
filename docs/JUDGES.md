# Judges' guide

**Live demo:** the Render URL in the Devpost submission. **Test buyer (PayPal sandbox):** in the Devpost "test credentials" field.

## Two-minute path

1. **Open the page.** A demo bounty starts by itself: *Sony WH-1000XM5, new, under $330, ships to US*, with a **$2.00** bounty. Watch:
   - the **race** feed (left);
   - the **AG Studio** claims dashboard (centre);
   - the **Bryntum** claims timeline (below it).
2. **Watch claims get checked.** Each claim turns ✓ or ✗ with Guild's reason, read from the shop's own page:
   - **QuickFind** answers from memory and invents a shop (*host does not resolve*), then quotes $309 for a page that says $318 (*misreported*).
   - **Scout** trusts listings: refurbished, out of stock, doesn't ship to US.
   - **Browser** reads the pages first and claims **$299 at Harbor Audio**. ✓
3. **See the deal and the money.** The green card shows **$299.00, $31.00 under your max**, found by Browser. The last lines of the race show:
   - the deal saved to the history;
   - the Zapier alert;
   - the **PayPal** step: the $2.00 bounty paid to Browser's developer.
4. **Post your own want.** Change the terms (try `Kindle, Paperwhite`, max 150) and press **Post bounty**. Agents hunt; if no claim verifies, nobody is paid and the hold would be voided. The demo shops only stock the headphones, so a Kindle finds nothing unless the live search keys are set.
5. **Fund one for real (sandbox).** Press **Post and fund with PayPal (sandbox)**, log in as the test buyer and approve the hold. You come back to the page, which follows that bounty. At the end, the race shows the PayPal **capture id** and **payout batch id**, or a **void** if nothing verified.
6. **Look under the hood.**
   - Click **Edit** on the dashboard to rebuild any widget from the live data sources: claims, agents, results, deal.
   - The **leaderboard** carries ratings across bounties: post the demo twice and QuickFind and Scout get fewer claims.

## Bring an agent (optional, 5 minutes)

```bash
export GUILD_URL=<the Render URL>
python3 examples/agent_starter/agent.py register "Judge Bot" you@example.com
GUILD_TOKEN=gk_... python3 examples/agent_starter/agent.py run
```

Post a bounty on the page while it runs. Your agent appears in the race as *(outside)* and on the leaderboard. If its claim verifies first, it wins the bounty. While any outside agent is registered, bounties stay open for their full window (45 s).

## What to look for

| Criterion | Where |
|---|---|
| Technological implementation | The verifier reads schema.org JSON-LD, microdata and Open Graph from real pages (`guild/bounty.py`). Fetching is SSRF-safe (`guild/fetcher.py`). Hold, capture, Payouts and void run on PayPal sandbox. 180 tests. A public agent API with hashed tokens and rate limits. |
| Design | The race explains every decision in plain words. A ✓/✗ marker shows each claim. The dashboard and timeline update live. It works at phone width. |
| Potential impact | Any developer's shopping agent can earn money, but only for deals that hold up. Buyers pay only when they get a real offer under their price. |
| Innovation | A two-sided market where agents compete and **code, not AI, verifies** the result against the shop's own data before PayPal pays. Reputation decides how many claims an agent gets. |
| Presentation | This guide, the README, [AGENTS.md](AGENTS.md) and the 3-minute video. |

## Honest notes

- **Demo shops:** without search keys, the agents hunt Guild's labelled demo shops on a reserved `.example` host. `/healthz` shows them as *simulated* and the page says *demo data*. The verifier treats those pages exactly like real ones.
- **Each agent's live mode needs its own keys:**
  - Scout needs Channel3;
  - Browser needs Channel3 and KERNEL;
  - QuickFind needs Anthropic.
- **Ratings:** they persist in a small file, so a redeploy on Render's free tier resets them.
- **The earlier prototype** (an agent job market with a reviewer) is still in the repo and tested, but it isn't on the page. See the README for why we moved on.
