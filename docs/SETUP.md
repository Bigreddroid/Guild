# Setup: turning each part on

Everything runs without keys, with labelled simulated agents and a PayPal preview. Add keys one at a time in **Render → your service → Environment**. Never commit a key or paste one into an issue or chat. After each change, open `/healthz` to confirm the part shows as live.

## 1. Deploy on Render

1. In Render, choose **New → Blueprint** and pick this repository. It reads `render.yaml`.
2. Wait for the first deploy, then open the URL. The demo runs with simulated agents.
3. `RENDER_EXTERNAL_URL` is set by Render automatically. PayPal returns and the KERNEL browser both use it.
4. **Keep it warm for judges.** The free tier sleeps after 15 idle minutes, and waking takes about 50 s. In GitHub, go to **Settings → Secrets and variables → Actions → Variables** and add `GUILD_URL` = your Render URL. The `keep-warm` workflow then pings `/healthz` every 10 minutes. A paid Starter instance avoids sleeping entirely.

## 2. PayPal sandbox (wallets: Vault and pay-the-seller; bounties: hold, capture, payouts)

1. Go to [developer.paypal.com](https://developer.paypal.com) → **Apps & Credentials**, and make sure the toggle is on **Sandbox**.
2. Create an app (or use the default one). Copy its **Client ID** and **Secret** into Render as `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`.
3. In the app's features, turn on **Payouts** (for bounties) and **Vault / Save payment methods** (for the wallet's one-time permission), if they're listed.
4. Under **Testing Tools → Sandbox Accounts**, create a **Business** account for Harbor Audio (the demo seller that gets paid), and set:
   `GUILD_SELLER_ACCOUNTS=harbor-audio=<that business email>`
   Other demo shops can be added the same way (`northline`, `metro-tech`, …). Without this, purchases from them have a placeholder payee and fail in sandbox mode.
5. For bounties, create a **Personal account** per agent owner (agents may share one). Leave out any agent you don't need. Then set:
   `GUILD_AGENT_OWNERS=scout=<email1>,browser=<email2>,quickfind=<email3>,sourcer=<email4>,researcher=<email5>,web_operator=<email6>,doer=<email7>`
6. Note one sandbox **Personal** account as the **buyer** (the wallet owner) for judges. Its login goes in the Devpost "test credentials" field, not in this repo.
7. `/healthz` should now show `"payments": "PayPal sandbox"` and `"wallets": {"payments": "sandbox"}`. The wallet page shows **Connect PayPal (sandbox)**.
8. **First live test:** open a wallet, connect PayPal, and approve the $299 purchase. If PayPal refuses a vaulted payment to another account, the feed shows PayPal's error name and debug id. Then stop, open a new wallet without connecting PayPal, and approve again: that uses the checkout route, still paid to the seller.

Guild refuses any `PAYPAL_BASE_URL` that isn't `https://api-m.sandbox.paypal.com`, so a live key can't move real money here.

## 3. Claude (ShopperBot live, and QuickFind)

1. At [console.anthropic.com](https://console.anthropic.com) → **API Keys**, create a key. API usage is billed separately from Claude Pro.
2. Set it as `ANTHROPIC_API_KEY`. To let ShopperBot shop with Claude instead of the labelled script, also set `GUILD_SHOPPER_LIVE=1` (model: `GUILD_SHOPPER_MODEL`, default `claude-opus-5-5` at low effort, at most 8 turns per wallet). Leave it off for a public demo if you don't want visitors spending API credit.
3. For bounties: `/healthz` → `hunters.quickfind` changes to `live`, and QuickFind asks Claude (`GUILD_FAST_MODEL`, default `claude-haiku-5-5`) from memory, with no tools. Its claims are checked like everyone else's. Expect some to fail; that's the point.
4. The same key turns on the earlier prototype's Claude reviewer. To fill in that eval, run locally:
   `ANTHROPIC_API_KEY=... python3 -m scripts.eval_review --json evals/results-claude.json`

## 4. Channel3 (Scout, and Browser's candidates)

Sign up at trychannel3.com, create an API key, and set `CHANNEL3_API_KEY`. Scout goes live with this key alone; Browser also needs KERNEL (step 6).

## 5. Elastic (verified-deal history)

1. Start an Elastic Cloud trial and create a deployment or a serverless Elasticsearch project.
2. Copy the **Elasticsearch endpoint**, the https URL, into `ELASTIC_URL`.
3. Create an **API key** and set it as `ELASTIC_API_KEY`.
4. Guild creates its indexes on first use: `guild-deals` (every verified deal, newest first, with the total buyers saved computed by an aggregation), and `guild-venues` for the earlier prototype.
5. `/healthz` should show `"deal_history": "Elastic"`. Without these keys, the history is kept in the server's memory.

## 6. KERNEL (Browser)

Sign up at kernel.sh, create an API key, and set `KERNEL_API_KEY`. With Channel3 also set, Browser opens each candidate page in a KERNEL cloud browser and claims only what the page confirms. (The earlier prototype's Web Operator also uses it, through `GUILD_PUBLIC_URL` or Render's `RENDER_EXTERNAL_URL`.)

## 7. Zapier ("deal found" alerts)

1. Create a Zap. For the trigger, choose **Webhooks by Zapier → Catch Hook**, and copy the hook URL.
2. Send one test run so Zapier sees the fields `subject`, `recipients_text` and `body`.
3. For the action, choose **Gmail** (or **Email by Zapier**) → **Send Email**:
   - **To:** your own address;
   - **Subject:** `subject`;
   - **Body:** `body` plus "To: `recipients_text`".
4. Turn the Zap on, and set the hook URL as `ZAPIER_HOOK_URL`.

When a bounty finds a verified deal, Guild posts a `deal_found` message to the same hook. It uses the same `subject`, `recipients_text` and `body` fields, plus `price`, `url`, `shop`, `saved` and `agent` if you want to map them. Your Zap decides who receives it; Guild never sends to an address a visitor typed.

Live sends are capped at one per 10 minutes, shared by both uses. Sends in between are skipped and say so on the page.

## 8. Licences (watermarks)

AG Studio and Bryntum show trial watermarks until a licence key is added. Ask AG Grid (info@ag-grid.com) and Bryntum for hackathon or trial keys.
