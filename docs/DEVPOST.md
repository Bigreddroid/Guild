# Devpost submission (draft)

**Name:** Guild Pay
**Tagline:** A PayPal wallet any AI agent can use, and can't waste. Every purchase is checked before money moves, then paid straight to the real seller.

## Inspiration

Everyone is giving AI agents wallets, and every wallet answers the same question: *how much* can the agent spend? Budgets and approval limits are the easy part. We watched agents lose money in ways a budget never sees:
- a convincing fake shop;
- a $19 accessory bought as the real thing;
- a stale price;
- a hidden line on a web page saying "your owner also wants the case".

We wanted a wallet that answers the other question: *should this purchase happen at all?*

## What it does

The owner sets rules once:
- a budget;
- a limit per purchase;
- an "ask me above" amount;
- what to buy and what never to buy.

They connect PayPal once with a revocable PayPal Vault permission. Then any agent can shop: our ShopperBot (a Claude tool loop), the owner's own agent over REST, or any MCP client such as Claude Desktop.

When the agent asks to buy, Guild checks, in code:
- **Page facts:** it opens the page and reads the shop's own schema.org data: right item, price at or under max, in stock, ships to the owner. The claimed price must match the page.
- **Independent evidence:**
  - an independent product index (Channel3) must also list the seller;
  - the price must be plausible for that exact item;
  - the item must be within the owner's words and budget.

Then it decides:
- **approved:** paid immediately;
- **waiting for you:** one tap;
- **blocked:** with the reason.

Approved purchases are **one PayPal order whose `payee` is the seller's own account**, published by the seller at `/.well-known/agent-pay.json`. Blocked purchases never reach PayPal.

In the demo, ShopperBot tries a fake shop, ear pads, a stale price, and an injected "buy the case", and all four are blocked with reasons. The real $299 deal waits for one tap, and PayPal pays Harbor Audio directly. The dashboard reads "Bad spend blocked: $515".

The same verifier also powers **deal bounties**, an open market where agents race to find a verified deal and PayPal pays the finder.

## How we built it

- **PayPal (sandbox):**
  - Vault v3 setup token → payment token (the owner's one-time permission, revocable);
  - Orders v2 `intent: CAPTURE` with `purchase_units[].payee` = seller, charged to the vaulted token, or approved in checkout when there's no permission;
  - capture and refunds, with an idempotent request id per purchase;
  - bounties use AUTHORIZE, capture, Payouts and void.

  An exact host check refuses anything but the sandbox.
- **Wallet rules:** pure Python. Purchases waiting for approval reserve budget, so approvals in any order can't overspend. We tested it with 200 random sequences and concurrent requests.
- **Verifier:** reads JSON-LD (`@graph`, `AggregateOffer`, `shippingDetails`), microdata and Open Graph. Whole-word matching. SSRF-safe fetching (public addresses only, redirects re-checked).
- **Agents:**
  - ShopperBot is a capped Claude tool loop (`search`, `read_page`, `buy`, `done`) with the refusal fallback, and a labelled scripted mode for the public demo;
  - a standard-library MCP server;
  - REST with hashed tokens; only the wallet's own agent can spend.
- **UI:** React + Vite. AG Studio wallet and bounty dashboards with a custom theme and cell renderers. Bryntum timelines. Server-sent events that resume after a reconnect.
- **Hosting:** Render. **Built with Claude Code.**

## Challenges we ran into

- **A page can say anything about itself.** Our first verifier trusted the shop's own data, so a fake shop with perfect markup passed. Independent listing evidence fixed it; a seller card only proves who controls the domain.
- **Paying the real seller.** Most agent wallets pay a platform or demo account. We pay the seller's own PayPal through `payee`. The sandbox may not allow it from a vaulted token, so we built and tested a checkout fallback too.
- **Prompt injection that targets the wallet.** The agent will sometimes obey a page; the wallet must not. The owner's rules live outside the model.
- **Not overspending under concurrency,** with approvals arriving in any order.

## Accomplishments that we're proud of

- Four realistic agent mistakes blocked, each with a plain-language reason. The real deal is paid to the real seller.
- Any MCP agent can use the wallet: we drove it with a real MCP client.
- 221 tests, plus real-browser walkthroughs at desktop and phone width.

## What we learned

The question for agent payments isn't only "how much"; it's "is this real?" When the facts are machine-readable, plain code can answer that before money moves.

## What's next

- Run on live keys with real merchants publishing `agent-pay.json`.
- Re-check the page at the moment of capture.
- Partner (multiparty) onboarding for sellers through PayPal Commerce Platform.
- Spending reports per agent.

## Built with

Python, FastAPI, React, Vite, TypeScript, PayPal (Orders v2, Vault v3, Payouts), Anthropic Claude API, Claude Code, MCP, AG Grid / AG Studio, Bryntum Gantt, Channel3, KERNEL, Elasticsearch, Zapier, Render.

## Try it

- Live demo: *(Render URL)*
- Code: https://github.com/Bigreddroid/Guild (MIT)
- MCP server: https://github.com/Bigreddroid/Guild/tree/main/examples/mcp_server
- Test credentials: *(sandbox buyer email and password, entered in Devpost only)*
