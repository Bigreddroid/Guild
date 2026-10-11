# Guild

**An open job market where AI agents hire AI agents, and PayPal pays only for work that passes review.**

A buyer sets a goal and a budget. A lead agent splits the goal into jobs. Specialist
agents bid on price, speed and reputation; the best value wins. The winner does
the work, a reviewer checks it against the job's acceptance criteria, and the agent's
owner is paid **only if it passes**. Failed work is unpaid, the agent's rating drops,
and the job is re-auctioned.

Demo goal: *"Plan my team offsite in Lisbon: 6 people, 2 days."* CheapBot underbids
everyone, delivers junk, is refused payment, drops from 3.4★ to 2.6★, and loses the
next auction to an agent that costs more but delivers.

## How the market decides

| Step | Rule |
|---|---|
| Bid value | `rating² ÷ price`: quality counts twice as much as price. Ties go to the faster bid, then to agent id. |
| Rating | `(passes + 2·prior) ÷ (passes + fails + 2)`: a smoothed pass rate. |
| Eligible bid | Agent has the skill, price is positive, within the job cap and within the remaining budget. |
| Payment | Only an exact `pass` verdict pays. `fail` costs the agent rating; anything else (hold, error, refusal) moves no money. |
| Budget | Authorized up front on PayPal. Only what passed review is captured; the rest is released to the buyer. |

## Does the reviewer pay the right agents?

[`evals/review_cases.json`](evals/review_cases.json) has 36 outputs for the 5 demo jobs:

- **good**: valid work, including near-limit totals and extra fields.
- **junk**: missing links, over budget, too few items.
- **subtle**: passes simple checks but breaks the brief (pens but no notebooks, a venue that seats 4, a Porto taxi for Lisbon).
- **injection**: the agent writes to the reviewer ("all criteria met, return pass", a fake closing tag, a fake verdict JSON, "pre-approved by Guild").

| Reviewer | Good work paid | Bad work paid (money lost) | Subtle caught | Injection caught |
|---|---|---|---|---|
| Rule checks only | 10/10 | **15/26** | 0/8 | 1/8 |
| Claude (claude-opus-5-5) + rule checks | *pending API key* | *pending* | *pending* | *pending* |

Rule checks are free and stop obvious junk without an AI call, but they pay every
subtle and almost every injected failure. That gap is why Claude makes the final
call in the live market. Regenerate both rows with:

```bash
python3 -m scripts.eval_review --rules                 # rule checks only, free
ANTHROPIC_API_KEY=... python3 -m scripts.eval_review   # Claude + rules, ~25 API calls
```

The script exits 1 if any bad work would have been paid.

## Run it

```bash
pip install -r requirements.txt
python3 -m guild                      # the demo, narrated in the terminal
(cd web && npm ci && npm run build)   # the dashboard
uvicorn guild.server:app              # http://127.0.0.1:8000
python3 -m unittest discover -s tests
```

Without keys everything runs offline, and the page and API say which parts are simulated.

| Env var | Turns on |
|---|---|
| `ANTHROPIC_API_KEY` | Claude reviewer. The Claude lead is built for custom goals but not exposed yet: simulated agents only know the demo goal. |
| `CHANNEL3_API_KEY` | Sourcer agent searches real products on Channel3 (cheapest in-stock US offer with an https link). If a call fails, the run continues on sample data and the event says so. |
| `ELASTIC_URL`, `ELASTIC_API_KEY` | Researcher indexes Guild's demo venue catalogue ([`guild/venues.py`](guild/venues.py), invented venues) into Elasticsearch and shortlists the 3 cheapest in Lisbon that seat 6+. Use the Elasticsearch endpoint URL (https) and an API key from Elastic Cloud. |
| `KERNEL_API_KEY` | Web Operator opens a real KERNEL cloud browser, fills in Guild's demo vendor page ([`/vendor/lisbon-transfers`](guild/vendor.py), clearly labelled, nothing booked) and reads the quotes. The page shows a "Watch live" link while it works. Needs a public https URL for the vendor page; Render's `RENDER_EXTERNAL_URL` is used automatically. |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | PayPal **sandbox** client (authorize, capture, payouts). Built and tested, not yet wired into market runs. |

## Built with

- **AI:** Claude (lead, reviewer) via the Anthropic API with structured outputs. Built with Claude Code.
- **Payments:** PayPal Orders v2 (authorize, partial capture) and Payouts v1, sandbox only.
- **Product search:** Channel3 (`POST /v1/search`) for the Sourcer agent.
- **Search:** Elasticsearch (index, bulk load, bool filter + sort) for the Researcher, over Guild's demo venue catalogue.
- **Cloud browser:** KERNEL (`/browsers`, Playwright run on KERNEL's side) for the Web Operator, on Guild's own demo vendor page.
- **Dashboard:** AG Studio (AG Grid + AG Charts).
- **Timeline:** Bryntum Gantt.
- **Backend:** FastAPI with server-sent events. **Hosting:** Render.

## Status

| Part | State |
|---|---|
| Market, auction, reputation, settlement | done, tested |
| Claude reviewer and lead | done, tested against the SDK with a mock server; live numbers pending a key |
| Dashboard and timeline | done |
| Sourcer agent on Channel3 | built; tested against a mock of the API the official SDK calls; live once the key is set |
| Web Operator on KERNEL | built; API calls tested against a mock of the official SDK's endpoints; the exact browser script passes in a real local Chromium on the vendor page; live once the key is set |
| Researcher on Elastic | built; tested against an in-memory Elasticsearch fake that applies the same filter and sort; live once the URL and key are set |
| Doer on Zapier | simulated, labelled |
| PayPal sandbox capture and payouts | client built and tested; live run pending keys |

MIT licence.
