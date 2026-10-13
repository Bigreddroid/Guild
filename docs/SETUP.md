# Setup: turning each part on

Everything runs without keys, with labelled simulated agents and a PayPal preview. Add keys one at a time in **Render → your service → Environment**. Never commit a key or paste one into an issue or chat. After each change, open `/healthz` to confirm the part shows as live.

## Go live in 10 minutes (the short version)

1. **Render:** New → Blueprint → this repository. Wait for the first deploy.
2. **PayPal sandbox app** ([developer.paypal.com](https://developer.paypal.com) → Apps & Credentials, toggle on **Sandbox**): copy the Client ID and Secret, and turn on **Payouts** in the app's features.
3. **Sandbox accounts** (Testing Tools → Sandbox Accounts): one **Business** account for the demo seller, one **Personal** account for the demo creator, and one **Personal** account as the buyer (its login goes in Devpost's test-credentials field, never in this repo).
4. **Render → your service → Environment:** set
   - `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`
   - `GUILD_SELLER_ACCOUNTS=harbor-audio=<business email>,maya-reviews=<creator email>`
5. **Check:** open `https://<your service>/api/readiness`. Every required item should say `"ok": true` (Payouts shows `null`: it can only be confirmed by the first payout). Anything missing comes with the exact fix.
6. **Run the live check** from any machine with Python 3:
   ```bash
   python3 -m scripts.live_check --url https://<your service>.onrender.com
   ```
   It opens a wallet, prints a PayPal link (pay $400 as the sandbox buyer), then checks on its own: ShopperBot's four bad purchases are blocked, the $299 one is paid out to the seller, the creator is paid on proof, and the unspent $61 is refunded. It writes `evidence/live-run.json` (ids and statuses; no keys, emails masked) and exits non-zero at the first failure, with PayPal's error name.

The sections below explain each part in more detail.

## 1. Deploy on Render

1. In Render, choose **New → Blueprint** and pick this repository. It reads `render.yaml`.
2. Wait for the first deploy, then open the URL. The demo runs with simulated agents.
3. `RENDER_EXTERNAL_URL` is set by Render automatically. PayPal returns and the KERNEL browser both use it.
4. **Keep it warm for judges.** The free tier sleeps after 15 idle minutes, and waking takes about 50 s. In GitHub, go to **Settings → Secrets and variables → Actions → Variables** and add `GUILD_URL` = your Render URL. The `keep-warm` workflow then pings `/healthz` every 10 minutes. A paid Starter instance avoids sleeping entirely.

## 2. PayPal sandbox (wallets: add money, pay sellers and creators, return unspent; bounties: hold, capture, payouts)

1. Go to [developer.paypal.com](https://developer.paypal.com) → **Apps & Credentials**, and make sure the toggle is on **Sandbox**.
2. Create an app (or use the default one). Copy its **Client ID** and **Secret** into Render as `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`. The app's own sandbox business account is where wallet top-ups land.
3. In the app's features, turn on **Payouts**. Wallets pay sellers and creators with it, and bounties pay the winning developer.
4. Under **Testing Tools → Sandbox Accounts**, create a **Business** account for Harbor Audio (the demo seller) and a **Personal** account for Maya Reviews (the demo creator), and set:
   `GUILD_SELLER_ACCOUNTS=harbor-audio=<business email>,maya-reviews=<personal email>`
   Other demo shops can be added the same way (`northline`, `metro-tech`, …). Without this, their payouts go to placeholder addresses and fail in sandbox mode, and the money is released back to the wallet.
5. For bounties, create a **Personal account** per agent owner (agents may share one). Leave out any agent you don't need. Then set:
   `GUILD_AGENT_OWNERS=scout=<email1>,browser=<email2>,quickfind=<email3>,sourcer=<email4>,researcher=<email5>,web_operator=<email6>,doer=<email7>`
6. Note one sandbox **Personal** account as the **buyer** (the wallet owner) for judges. Its login goes in the Devpost "test credentials" field, not in this repo.
7. `/healthz` should now show `"payments": "PayPal sandbox"` and `"wallets": {"payments": "sandbox"}`. A new wallet starts empty, and the balance card offers **Pay with PayPal**.
8. **First live test** (or run `scripts/live_check.py`, above, which does the same and records the evidence):
   1. Open the page, pick $400 and tap **Pay with PayPal**. Log in as the buyer and pay. Back on the page, the balance shows $400 and ShopperBot starts.
   2. Approve the $299 purchase. The activity row shows the payout batch id; check that Harbor Audio's sandbox account received $299.
   3. Book the demo creator ($40) and send **The real post**. Check that Maya Reviews' sandbox account received $40.
   4. Tap **Return unspent**. Check the buyer's sandbox account for the refund of the rest ($61).

   If a payout fails, the row shows PayPal's error name and debug id, and the money stays in the wallet.

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
