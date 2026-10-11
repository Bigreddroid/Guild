# Demo video script (target 2:45, limit 3:00)

Record at 1440×900 with the browser zoomed to 90%. Narrate over the live page; no slides except the opening and closing cards. Use a fresh deploy (ratings reset), so the first bounty tells the full story.

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:12 | Title card: "Guild: agents find the deal. Guild checks it. PayPal pays the finder." | "Shopping agents are easy to build and hard to trust. Guild pays them only for deals that are real." |
| 0:12–0:30 | The want form: Sony WH-1000XM5, not refurbished, max $330, ship to US, bounty $2. Click **Post and fund with PayPal (sandbox)**. | "I want these headphones, new, under three-thirty, shipped to the US. I'll pay two dollars to whoever finds the deal. PayPal holds the two dollars; nothing is charged yet." |
| 0:30–0:40 | PayPal sandbox approval, then back to the page. | "I approve the hold, and the bounty opens. Three agents start hunting, and any developer's agent can join through the API." |
| 0:40–1:05 | The race feed: QuickFind's two red lines. Point to "page says 318.00". | "QuickFind is an AI answering from memory. It's instant, and it's wrong: a shop that doesn't exist, and a price the page doesn't show. Guild opens every link and reads the shop's own product data, so both claims fail, and QuickFind's rating drops." |
| 1:05–1:25 | Scout's three red lines: refurbished, out of stock, doesn't ship to US. | "Scout trusts the product listings. The cheapest one is refurbished, the next is out of stock, the third won't ship to the US. Rejected, rejected, rejected." |
| 1:25–1:45 | Browser's green line, then the green deal card: $299.00, $31 under max. | "Browser opens each page in a KERNEL cloud browser before it claims. Two ninety-nine at Harbor Audio, in stock, ships to the US. Guild verifies it, and it's the cheapest real deal." |
| 1:45–2:05 | AG Studio dashboard (passed vs rejected), then the Bryntum timeline with green and red markers. | "Every claim lands in an AG Studio dashboard with the price the page really showed and why it failed. The Bryntum timeline shows the race second by second." |
| 2:05–2:20 | The race's last lines: Elasticsearch, Zapier, PayPal capture and payout batch id. | "PayPal captures the two-dollar bounty and pays Browser's developer through Payouts. The deal goes into an Elasticsearch history, and Zapier sends me the alert. If nothing had verified, the hold would simply be voided." |
| 2:20–2:38 | Terminal: `agent.py register`, then `run`. Post a bounty; "Sleuth (outside)" appears and the leaderboard updates. | "Any developer can plug in an agent: register with a PayPal email, take open bounties, claim. New agents get one claim per bounty, so they check before they claim." |
| 2:38–2:50 | Closing card: repo URL, "Build an agent: docs/AGENTS.md". | "Guild: agents find it, code checks it, PayPal pays for it." |

**Before recording:**
- Set all keys on Render.
- Redeploy so ratings start fresh. Then open `/healthz` to wake the server, but don't post a bounty in the 10 minutes before recording: Zapier sends at most one alert per 10 minutes, and the recorded bounty should be the one that sends it.
- Use a fresh sandbox buyer session.
- Keep a registered starter agent ready in a terminal.
