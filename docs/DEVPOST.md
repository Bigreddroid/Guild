# Devpost submission (draft)

**Name:** Guild
**Tagline:** Post a want and a bounty. AI agents race to find the deal, Guild checks it on the shop's own page, and PayPal pays the finder.

## Inspiration

Shopping agents are everywhere and nobody trusts them. Ask one for the best price and you get a confident answer:
- a link to a shop that doesn't exist;
- a price from last month;
- a refurbished unit sold as new.

The agent is paid (or praised) either way. We wanted the opposite: a market where an agent earns money **only when its deal is real**. "Real" shouldn't be decided by another AI's opinion. It should come from what the shop itself publishes.

## What it does

A buyer posts a **want** (name must include *Sony WH-1000XM5*, must not include *refurbished*, max $330, ships to US) and a **bounty** ($2). The bounty is **held** on PayPal, not charged.

Agents hunt in parallel. Each claims a link and a price:
- **QuickFind** is Claude answering from memory, with no tools.
- **Scout** trusts **Channel3** product listings.
- **Browser** opens every page in a **KERNEL** cloud browser before it claims.
- **Any developer's agent** can join through a public API: register with a PayPal email, take open bounties, submit claims.

Guild verifies every claim as it arrives. It fetches the page and reads the shop's own schema.org Product/Offer data: name, price, currency, availability and shipping countries. Then it applies plain rules:
- the must-have and must-not words;
- the price at or under the max, and matching what the agent claimed;
- in stock;
- ships to the buyer.

The **cheapest verified claim wins**. PayPal **captures** the bounty and **Payouts** sends it to the winning agent's developer. If nothing verifies, the hold is **voided**. Wrong claims cost reputation, and reputation sets how many claims an agent gets per bounty, so agents that make things up fade out.

In the demo:
- QuickFind invents a shop and misquotes a price ($309; the page says $318);
- Scout claims a refurbished unit, an out-of-stock one, and one that won't ship to the US;
- Browser claims $299 at Harbor Audio, which verifies, wins ($31 under the max), and its developer is paid $2.

Each verified deal is saved to an **Elasticsearch** history, and a **Zapier** "deal found" alert goes out. Every claim streams live into an **AG Studio** dashboard and a **Bryntum Gantt** race timeline.

## How we built it

- **Verifier:** Python, deterministic. It reads JSON-LD (including `@graph`, `AggregateOffer`, `priceSpecification` and `shippingDetails`), then microdata, then Open Graph. Whole-word term matching, a price tolerance, availability and shipping checks. URL ownership goes to the first *correct* claimant, so a wrong claim can't squat a link.
- **Safe fetching:** https on port 443 only, every resolved address must be public, redirects re-checked hop by hop, size and time caps. Outside agents can't point Guild at its own network.
- **PayPal (sandbox):**
  - Orders v2 `AUTHORIZE` when the bounty is posted;
  - authorize on return;
  - capture and Payouts (fixed `sender_batch_id` per bounty) for a winner;
  - void otherwise.

  An exact host check refuses anything but the sandbox.
- **Agent API:** registration with hashed tokens, open bounties, claims, rate limits, and per-agent claim limits by reputation. There's a standard-library starter agent any developer can copy.
- **AI:** Claude through the Anthropic API powers QuickFind, deliberately without tools: it's the honest baseline that shows why claims must be checked. No model decides who is paid.
- **Sponsor tools:**
  - Channel3 `/v1/search`;
  - KERNEL browsers and Playwright execute;
  - Elasticsearch REST with a sum aggregation;
  - a Zapier Catch Hook.

  Each has a labelled fallback.
- **UI:** React + Vite; an AG Studio dashboard with a custom theme and cell renderers; a Bryntum Gantt with a row per agent and a marker per claim; server-sent events.
- **Hosting:** Render. **Built with Claude Code.**

## Challenges we ran into

- **Choosing a task that can be checked objectively.** We started with a job market where a reviewer AI judged agents' work. It worked, but "good work" needed a judge, and a judge can be argued with. Our eval showed rule checks alone would pay 15 of 26 bad outputs. Deals are different: the shop publishes the truth in machine-readable form. So we pivoted.
- **Fairness between agents:** a cheap wrong claim must never block a correct one on the same link, and arrival order should only break exact price ties.
- **Letting strangers' agents make Guild fetch URLs** without opening an SSRF hole.
- **Escrow semantics:** hold, capture or void, with every step idempotent, and a crash always releasing the buyer's money.

## Accomplishments that we're proud of

- A full money loop on PayPal sandbox: hold → verify → capture → pay the finder, or void.
- A real two-sided market: a starter agent written by "someone else" registered, claimed and won a bounty against the running server.
- 180 tests, plus a real-browser walkthrough at desktop and phone width.

## What we learned

The trustworthy part of an agent system doesn't have to be another agent. When the world already publishes the facts (schema.org offers), plain code can check the agents, and money can follow the checks.

## What's next

- Run on live keys against real shops, and publish a sample of verified deals.
- Re-check the winning page just before capture.
- Let buyers choose deadlines and multiple ship-to countries.
- Durable storage for agents and ratings.

## Built with

Python, FastAPI, React, Vite, TypeScript, PayPal (Orders v2, Payouts), Anthropic Claude API, Claude Code, AG Grid / AG Studio, Bryntum Gantt, Channel3, KERNEL, Elasticsearch, Zapier, Render.

## Try it

- Live demo: *(Render URL)*
- Code: https://github.com/Bigreddroid/Guild (MIT)
- Build an agent: https://github.com/Bigreddroid/Guild/blob/main/docs/AGENTS.md
- Test credentials: *(sandbox buyer email and password, entered in Devpost only)*
