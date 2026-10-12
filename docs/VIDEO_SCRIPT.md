# Demo video script (target 2:45, limit 3:00)

Record at 1440×900 with the browser zoomed to 90%. Narrate over the live page; no slides except the opening and closing cards. Use `GUILD_SHOPPER_STEP_S=1.5` so each attempt lands on screen.

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:12 | Title card: "Guild Pay: a wallet your AI agent can't waste." | "Giving an AI agent a budget is easy. Stopping it from wasting the budget is the hard part." |
| 0:12–0:30 | The wallet form: $400, $350 per purchase, ask me above $250, Sony WH-1000XM5, never refurbished or a case. Click **Connect PayPal (sandbox)**, approve once, and come back. | "I give my shopping agent four hundred dollars and my rules. I connect PayPal once, and I can revoke it any time." |
| 0:30–0:50 | ShopperBot's first line, then the $149 card turns red: ✗ seller listed. | "ShopperBot finds a hundred-and-forty-nine-dollar deal. The page looks perfect, and so does the shop's payment card. But no independent product index has ever heard of this shop. Blocked, before PayPal is even called." |
| 0:50–1:05 | The $19 card: ✗ plausible price, 6% of the usual. | "Nineteen dollars for the same headphones? It's ear pads. Guild compares the price with what this exact item usually costs." |
| 1:05–1:15 | The $309 card: ✗ price matches, the page says $318. | "A stale listing: the agent says three-oh-nine, the shop says three-eighteen. Blocked." |
| 1:15–1:35 | The $29 case card: ✗ right item. Show the hidden text in the page source. | "Then a hidden line on a shop page tells the agent its owner wants a case too, and it obeys. The wallet doesn't. Page text can't change my rules." |
| 1:35–1:55 | The amber card: $299 at Harbor Audio, waiting for you. Tap **Approve and pay the seller**. The feed shows PayPal sandbox paid $299 to Harbor Audio, with the order id. | "The real deal passes every check. It's over my two-fifty limit, so it waits for me. One tap, and PayPal pays Harbor Audio's own account directly." |
| 1:55–2:15 | AG Studio: Bad spend blocked $515, the decisions donut, every request. The Bryntum timeline with markers. | "In one minute my agent tried to spend five hundred and fifteen dollars badly. The AG Studio dashboard and the Bryntum timeline show every request and why." |
| 2:15–2:35 | Claude Desktop with the Guild Pay MCP server: "buy the headphones" comes back pending or blocked with reasons. | "Any agent can use it. Here's Claude Desktop through our MCP server, same wallet, same rules." |
| 2:35–2:48 | Closing card: repo URL, "Verify before pay." | "Guild Pay: your agent asks, Guild checks, PayPal pays the real seller." |

**Before recording:**
- Set the PayPal sandbox keys and `GUILD_SELLER_ACCOUNTS` on Render.
- Use a fresh sandbox buyer session.
- Configure Claude Desktop with a wallet for its agent.
- If live Claude is on (`GUILD_SHOPPER_LIVE=1`), rehearse once: its attempts vary. The scripted run is reproducible.
