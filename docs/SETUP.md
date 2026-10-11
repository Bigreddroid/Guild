# Setup: turning each part on

Everything runs without keys, with labelled simulated agents and a PayPal preview. Add keys one at a time in **Render → your service → Environment**. Never commit a key or paste one into an issue or chat. After each change, open `/healthz` to confirm the part shows as live.

## 1. Deploy on Render

1. In Render, choose **New → Blueprint** and pick this repository. It reads `render.yaml`.
2. Wait for the first deploy, then open the URL. The demo runs with simulated agents.
3. `RENDER_EXTERNAL_URL` is set by Render automatically. PayPal returns and the KERNEL browser both use it.

## 2. PayPal sandbox (funding, capture, payouts)

1. Go to [developer.paypal.com](https://developer.paypal.com) → **Apps & Credentials**, and make sure the toggle is on **Sandbox**.
2. Create an app (or use the default one). Copy its **Client ID** and **Secret** into Render as `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`.
3. In the app's features, turn on **Payouts**.
4. Under **Testing Tools → Sandbox Accounts**, create **4 Personal accounts**, one per agent owner. Then set:
   `GUILD_AGENT_OWNERS=sourcer=<email1>,researcher=<email2>,web_operator=<email3>,doer=<email4>`
5. Note one sandbox **Personal** account as the **buyer** for judges. Its login goes in the Devpost "test credentials" field, not in this repo.
6. `/healthz` should now show `"payments": "PayPal sandbox", "funding": true`, and the page shows **Fund with PayPal (sandbox)**.

Guild refuses any `PAYPAL_BASE_URL` that isn't `https://api-m.sandbox.paypal.com`, so a live key can't move real money here.

## 3. Claude reviewer

1. At [console.anthropic.com](https://console.anthropic.com) → **API Keys**, create a key. API usage is billed separately from Claude Pro.
2. Set it as `ANTHROPIC_API_KEY`. The reviewer label changes to `Claude (claude-opus-5-5) + rule checks`.
3. Optionally, fill in the README's eval row by running locally:
   `ANTHROPIC_API_KEY=... python3 -m scripts.eval_review --json evals/results-claude.json`

## 4. Channel3 (Sourcer)

Sign up at trychannel3.com, create an API key, and set `CHANNEL3_API_KEY`.

## 5. Elastic (Researcher)

1. Start an Elastic Cloud trial and create a deployment or a serverless Elasticsearch project.
2. Copy the **Elasticsearch endpoint**, the https URL, into `ELASTIC_URL`.
3. Create an **API key** and set it as `ELASTIC_API_KEY`.
4. On the first run, Guild creates the `guild-venues` index and loads the demo catalogue.

## 6. KERNEL (Web Operator)

Sign up at kernel.sh, create an API key, and set `KERNEL_API_KEY`. On Render nothing else is needed. Elsewhere, set `GUILD_PUBLIC_URL` to the site's public https address, so the cloud browser can reach `/vendor/lisbon-transfers`.

## 7. Zapier (Doer)

1. Create a Zap. For the trigger, choose **Webhooks by Zapier → Catch Hook**, and copy the hook URL.
2. Send one test run so Zapier sees the fields `subject`, `recipients_text` and `body`.
3. For the action, choose **Gmail** (or **Email by Zapier**) → **Send Email**:
   - **To:** your own address;
   - **Subject:** `subject`;
   - **Body:** `body` plus "To: `recipients_text`".
4. Turn the Zap on, and set the hook URL as `ZAPIER_HOOK_URL`.

Live sends are capped at one per 10 minutes. Runs in between use labelled sample output.

## 8. Licences (watermarks)

AG Studio and Bryntum show trial watermarks until a licence key is added. Ask AG Grid (info@ag-grid.com) and Bryntum for hackathon or trial keys.
