# Devpost submission (draft)

**Name:** Guild
**Tagline:** AI agents hire AI agents, and PayPal pays only for work that passes review.

## Inspiration

Agents can already spend money. Nothing makes them *earn* it. Hand an agent a budget and a goal today, and it pays whoever answers first, including another agent that answers fast, cheap and wrong. We wanted a market where an agent's money follows the quality of its work, the way it does for people: you bid, you deliver, someone checks, and you're paid only if it's right.

## What it does

A buyer sets a goal and a budget. The budget is approved on PayPal and **held, not charged**. A lead agent turns the goal into jobs with acceptance criteria. Specialist agents bid, and the market picks the best value: `rating² ÷ price`, so a reliable agent beats a cheaper unreliable one. The winner does the work with real tools:
- **Channel3** product search;
- an **Elasticsearch** venue index;
- a **KERNEL** cloud browser that fills in a booking form;
- a **Zapier** action that sends the team email.

A reviewer (rule checks, then Claude) checks the work. **Pass:** the agent's owner is owed the bid. **Fail:** nothing is paid, its rating drops, and the job is re-auctioned. At the end, Guild **captures only what was earned** from the PayPal hold. `final_capture` releases the rest to the buyer, and a single **PayPal Payouts** batch pays each agent's owner.

In the demo, CheapBot underbids at $0.10, delivers junk, gets nothing, and loses the next auction. The buyer's $3.00 hold becomes a $2.00 capture and a $1.00 release. Everything streams live into an **AG Studio** dashboard and a **Bryntum Gantt** timeline.

## How we built it

- **Market:** Python with deterministic rules. Bid eligibility, value ranking with order-independent tie-breaks, smoothed reputation, and settlement that pays only on an exact `pass`, never past the budget and never twice.
- **PayPal (sandbox):**
  - Orders v2 `AUTHORIZE` with return and cancel URLs;
  - authorize on return;
  - capture with `final_capture`;
  - void when nothing passed;
  - Payouts with a fixed `sender_batch_id` per run.

  An exact host check refuses anything but the sandbox.
- **AI:** Claude through the Anthropic API with structured outputs, as the reviewer and the lead for custom goals. Agent output is fenced as untrusted data, so an agent can't talk its way into being paid. We built a 36-case eval of good work, junk, subtle failures and prompt-injection attempts. It shows rule checks alone would pay 15 of 26 bad outputs.
- **Sponsor tools:** thin clients written against each official SDK's endpoints: Channel3 `/v1/search`, KERNEL `/browsers` and Playwright execute, Elasticsearch REST, and a Zapier Catch Hook. Each has a labelled fallback, so the demo never fakes a live result.
- **UI:** React + Vite, an AG Studio dashboard with a custom theme and cell renderers, a Bryntum Gantt timeline, and server-sent events.
- **Hosting:** Render.
- **Built with Claude Code.**

## Challenges we ran into

- **Making "pay only for good work" hold up against the worker.** The reviewer reads text written by the agent that wants to be paid. We fence it, escape it and cap it, and the market ignores any verdict that isn't an exact `pass`.
- **Escrow semantics.** Authorize up front, capture partially, release the remainder, void on failure, and make every step idempotent.
- **Live UI libraries.** The React wrapper re-applied a fresh `project` prop on every render and wiped the Gantt. Stable props fixed it.

## Accomplishments that we're proud of

- A complete money loop on PayPal sandbox: hold → capture passed work → release the rest → pay owners.
- A measurable reviewer: the eval set and a script that fails the build if bad work would be paid.
- 124 tests, including a real-browser run of the exact script KERNEL executes.

## What we learned

Reputation has to be priced in, not just displayed. Squaring the rating in the bid value is enough to make a cheap, bad agent lose on its own.

## What's next

- Open the market to third-party agents with published capability cards.
- Use the Claude lead for any goal.
- Add disputes and refunds after delivery.
- Make reputation portable across runs.

## Built with

Python, FastAPI, React, Vite, TypeScript, PayPal (Orders v2, Payouts), Anthropic Claude API, Claude Code, AG Grid / AG Studio, Bryntum Gantt, Channel3, KERNEL, Elasticsearch, Zapier, Render.

## Try it

- Live demo: *(Render URL)*
- Code: https://github.com/Bigreddroid/Guild (MIT)
- Test credentials: *(sandbox buyer email and password, entered in Devpost only)*
