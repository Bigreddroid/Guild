# Guild Pay MCP server

Give any MCP agent (Claude Desktop, Claude Code, your own) a wallet it can spend from, within its owner's rules. It's one file, using only the Python standard library.

1. Register your agent: `POST /api/agents {name, payout_email}` returns a `gk_…` token.
2. The owner creates a wallet for that agent: `POST /api/wallets {"agent": "<agent_id>", "budget": "400", "max_per_purchase": "350", "approve_above": "250", "must_have": ["Sony", "WH-1000XM5"]}`. They keep the owner key to approve or stop.
3. Run the server with `GUILD_URL`, `GUILD_TOKEN` and `GUILD_WALLET` set:

```json
{"mcpServers": {"guild-pay": {"command": "python3", "args": ["/path/to/guild_pay_mcp.py"],
  "env": {"GUILD_URL": "https://<guild-host>", "GUILD_TOKEN": "gk_...", "GUILD_WALLET": "<wallet id>"}}}}
```

**Tools:**
- `wallet_status`
- `search_products`: Guild's labelled demo index only.
- `buy {url, item, price, category?}`
- `book_promotion {creator, channel, paypal_email, amount, link, words?, deadline_minutes?, category?}`: always waits for the owner's OK, because the agent chose who gets paid.
- `send_proof {purchase_id, url}`: the creator is paid only if the live post is on their channel, links to `link`, mentions `words` and visibly discloses the ad.
- `subscribe_tool {url, item, price, every_minutes?, category?}`: the first payment waits for the owner. Every later period is checked again before paying, and only the owner can cancel.
- `report_tool_use {renewal_id}`: a tool unused for two periods is flagged to the owner.

The agent can only ask to buy. Guild checks the seller against an independent listing, the page price, the owner's want and the budget, then answers *paid*, *pending* (waiting for the owner) or *blocked* with reasons. PayPal pays the seller's own account only for purchases that are approved.
