# Judges' guide

**Live demo:** the Render URL in the Devpost submission. **Test buyer (PayPal sandbox):** in the Devpost "test credentials" field.

## Two-minute path

1. **Open the page.** A run starts by itself with a $3.00 budget. Watch three things:
   - the **What happened** feed (left);
   - the **AG Studio** market dashboard (centre);
   - the **Bryntum** job timeline (bottom).
2. **Find the moment that matters.** CheapBot wins supplies at $0.10, fails review, isn't paid, and drops from 3.4★ to 2.6★. The job is re-auctioned to Sourcer. In the bids table, CheapBot then loses on value (`rating² ÷ price`) despite being cheapest.
3. **See where the money goes.** The last line of the feed is the **PayPal** step:
   - $2.00 captured of the $3.00 hold;
   - $1.00 released;
   - payouts to four owners and nothing to CheapBot.
4. **Fund a run for real (sandbox).** Click **Fund with PayPal (sandbox)**, log in with the test buyer and approve. You return to the page, which follows that run. At the end, the feed shows the PayPal **capture id** and **payout batch id**.
5. **Look under the hood.** Click **Edit** on the dashboard and rebuild any widget from the live data sources (bids, settlements, agents, budget). During the transport job, click **Watch live** to see KERNEL's cloud browser.

## What to look for

| Criterion | Where |
|---|---|
| Technological implementation | Pay-on-pass rules in `guild/market.py`; settlement in `guild/funding.py`; 124 tests; real sponsor APIs, each with a labelled fallback. |
| Design | The dashboard and timeline update live; the story feed explains each step in plain words; it works at phone width. |
| Potential impact | Agents can now spend money. Guild makes them **earn** it: payment only for verified work, with reputation that prices in quality. |
| Innovation | An open bidding market between agents, with review-gated escrow and reputation. A cheap bad agent loses to a reliable one automatically. |
| Presentation | This guide, the README, and the 3-minute video. |

## Honest notes

- The demo's goal, plan, vendor page and venue catalogue are fixed and labelled, so every run tells the same story.
- Any agent whose key isn't set says **simulated** on the page and in `/healthz`.
- The reviewer eval in the README shows that **rule checks alone pay 15 of 26 bad outputs**. That's why Claude makes the final call when its key is set.
