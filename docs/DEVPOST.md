# Devpost submission (draft)

**Name:** Guild Pay
**Tagline:** A prepaid PayPal wallet for your AI: load it, and your AI can only spend what's in it, on purchases Guild has checked.

## Inspiration

Everyone is giving AI agents wallets, and every wallet answers the same question: *how much* can the agent spend? Budgets and approval limits are the easy part. We watched agents lose money in ways a budget never sees:
- a convincing fake shop;
- a $19 accessory bought as the real thing;
- a stale price;
- a hidden line on a web page saying "your owner also wants the case".

We wanted a wallet that answers the other question: *should this purchase happen at all?* And we didn't want to hand an agent a card on file to find out.

## What it does

The owner sets rules once:
- the most the wallet can hold;
- spending limits per envelope (shopping, marketing, ...);
- a limit per purchase;
- an "ask me above" amount;
- what to buy and what never to buy.

Then they **load the wallet with PayPal** ("Add money", a normal PayPal checkout). Every wallet starts empty, and there is no card on file and no standing permission: the AI can only spend what was loaded. Any agent can shop with it: our ShopperBot (a Claude tool loop), the owner's own agent over REST, or any MCP client such as Claude Desktop.

When the agent asks to buy, Guild checks, in code:
- **Page facts:** it opens the page and reads the shop's own schema.org data: right item, price at or under max, in stock, ships to the owner. The claimed price must match the page.
- **Independent evidence:**
  - an independent product index (Channel3) must also list the seller;
  - the price must be plausible for that exact item;
  - the item must be within the owner's words, limits and the money left.

Then it decides:
- **approved:** paid immediately;
- **needs your OK:** one tap;
- **blocked:** with the reason.

Approved purchases are paid **from the loaded money to the seller's own PayPal account** with PayPal Payouts, using the account the seller publishes at `/.well-known/agent-pay.json`. Blocked purchases never reach PayPal. **Return unspent** refunds whatever is left to the owner's PayPal, and stopping or closing the wallet does it automatically.

The wallet also does **marketing that pays on proof**: book a creator from the marketing envelope, and the money is held inside the wallet until the creator's post is really live on their channel, links to the owner's page, shows an ad disclosure and names the product. Only then is the creator paid. A copied post, a hidden #ad or a missing link pays nothing.

In the demo, the owner adds demo money (or pays with PayPal in sandbox), and ShopperBot starts. It tries a fake shop, ear pads, a stale price, and an injected "buy the case", and all four are blocked with reasons. The real $299 deal waits for one tap, and Harbor Audio is paid directly. The page reads "Guild stopped $515.00 of bad spending before any money moved."

The same verifier also powers **deal bounties**, an open market where agents race to find a verified deal and PayPal pays the finder.

## How we built it

- **PayPal (sandbox):**
  - Orders v2 `intent: CAPTURE` checkout to load the wallet, paid to Guild's own sandbox account;
  - Payouts to pay sellers and creators, one item per purchase, keyed by the purchase id so it can't be paid twice;
  - Refunds of the top-up captures to return unspent money, newest first, each partial refund with its own request id;
  - bounties use AUTHORIZE, capture, Payouts and void.

  An exact host check refuses anything but the sandbox.
- **Wallet rules:** pure Python, with envelopes. Purchases waiting for approval and promotions waiting for proof reserve money, so approvals in any order can't overspend. We tested it with 200 random sequences and concurrent requests.
- **Verifier:** reads JSON-LD (`@graph`, `AggregateOffer`, `shippingDetails`), microdata and Open Graph. Whole-word matching. SSRF-safe fetching (public addresses only, redirects re-checked).
- **Agents:**
  - ShopperBot is a capped Claude tool loop (`search`, `read_page`, `buy`, `done`) with the refusal fallback, and a labelled scripted mode for the public demo;
  - a standard-library MCP server;
  - REST with hashed tokens; only the wallet's own agent can spend.
- **UI:** React + Vite. A clean wallet page: a balance card, a "Needs your OK" card, one Activity list with tap-to-expand ✓/✗ checks, and cards for your AI, pay-on-proof marketing and your rules. A budget card: what to watch and one-tap limit changes, with a summary Claude writes from the numbers only (never page text). Under it, an AG Grid statement of every line (search, sort, filters, CSV export). The bounties tab has an AG Studio dashboard with a custom theme and cell renderers, and a Bryntum timeline. Server-sent events that resume after a reconnect.
- **Hosting:** Render. **Built with Claude Code.**

## Challenges we ran into

- **A page can say anything about itself.** Our first verifier trusted the shop's own data, so a fake shop with perfect markup passed. Independent listing evidence fixed it; a seller card only proves who controls the domain.
- **Paying the real seller without a card on file.** Most agent wallets pay a platform or demo account, or keep a standing permission to charge the owner. We moved to a prepaid model: the owner loads money once, and Guild pays the seller's own PayPal account from it with Payouts. The worst an agent can do is spend what was loaded, inside the rules.
- **Prompt injection that targets the wallet.** The agent will sometimes obey a page; the wallet must not. The owner's rules live outside the model.
- **Not overspending under concurrency,** with approvals arriving in any order.

## Accomplishments that we're proud of

- Four realistic agent mistakes blocked, each with a plain-language reason. The real deal is paid to the real seller.
- A creator is paid only for a post that is really live and really disclosed.
- Unspent money always goes back to the owner.
- Any MCP agent can use the wallet: we drove it with a real MCP client.
- 252 tests.

## What we learned

The question for agent payments isn't only "how much"; it's "is this real?" When the facts are machine-readable, plain code can answer that before money moves. And a prepaid wallet makes "how much" simple: the agent can't spend what isn't there.

## What's next

- Run on live keys with real merchants publishing `agent-pay.json`.
- Re-check the page at the moment of payment.
- Partner (multiparty) onboarding for sellers through PayPal Commerce Platform.
- Spending reports per agent, and matching the wallet's ledger to PayPal Transaction Search once live.

## Built with

Python, FastAPI, React, Vite, TypeScript, PayPal (Orders v2, Payouts, Refunds), Anthropic Claude API, Claude Code, MCP, AG Grid / AG Studio, Bryntum Gantt, Channel3, KERNEL, Elasticsearch, Zapier, Render.

## Try it

- Live demo: *(Render URL)*
- Code: https://github.com/Bigreddroid/Guild (MIT)
- MCP server: https://github.com/Bigreddroid/Guild/tree/main/examples/mcp_server
- Test credentials: *(sandbox buyer email and password, entered in Devpost only)*
