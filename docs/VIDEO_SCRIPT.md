# Demo video script (target 2:45, limit 3:00)

Record at 1440×900 with the browser zoomed to 90%. Narrate over the live page; no slides except the opening and closing cards. Use `GUILD_SHOPPER_STEP_S=1.5` so each attempt lands on screen.

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:12 | Title card: "Guild Pay: a prepaid PayPal wallet for your AI." | "Giving an AI agent money is easy. Making sure it can't waste it is the hard part." |
| 0:12–0:30 | The empty wallet: "Available to your AI: $0.00". Pick $400, tap **Pay with PayPal**, pay as the sandbox buyer, come back to $400. | "I load my AI's wallet with four hundred dollars through PayPal. No card on file: it can only ever spend what I put in." |
| 0:30–0:50 | ShopperBot's first note, then the $149 row turns red: Blocked. Expand it: ✗ Real seller. | "ShopperBot finds a hundred-and-forty-nine-dollar deal. The page looks perfect. But no independent product index has ever heard of this shop. Blocked, before PayPal is even called." |
| 0:50–1:05 | The $19 row, expanded: ✗ Plausible price, 6% of the usual. | "Nineteen dollars for the same headphones? It's ear pads. Guild compares the price with what this exact item usually costs." |
| 1:05–1:15 | The $318 row: ✗ Price matches page. | "A stale listing: the agent says three-oh-nine, the shop says three-eighteen. Blocked." |
| 1:15–1:30 | The $29 case row: ✗ Right item. Show the hidden text in the page source. | "Then a hidden line on a shop page tells the agent its owner wants a case too, and it obeys. The wallet doesn't. Page text can't change my rules." |
| 1:30–1:45 | "Needs your OK": $299 at Harbor Audio. Tap **Approve**. The row turns Paid; the balance shows $101. The green banner: "Guild stopped $515.00 of bad spending". | "The real deal passes every check. It's over my two-fifty limit, so it waits for me. One tap, and Harbor Audio's own PayPal account is paid from the wallet." |
| 1:45–2:15 | **Pay a creator on proof**: book Maya Reviews for $40 (On hold). Send **Hidden #ad**: refused, still on hold. Send **The real post**: Paid. | "It does marketing too. I book a creator for forty dollars; the money waits in my wallet. A post that hides the ad label pays nothing. The real post, live on her channel, linking to me, disclosed: paid." |
| 2:15–2:30 | Claude Desktop with the Guild Pay MCP server: "buy the headphones" comes back pending or blocked with reasons; "book Maya to post about it" waits for the owner's OK. | "Any agent can use it. Here's Claude Desktop through our MCP server, same wallet, same rules." |
| 2:30–2:42 | Tap **Return unspent**: the rest goes back to PayPal; balance $0. | "And whatever my AI doesn't spend comes straight back to me." |
| 2:42–2:50 | Closing card: repo URL, "Load it. It can't waste it." | "Guild Pay: your AI asks, Guild checks, PayPal pays the real seller." |

**Before recording:**
- Set the PayPal sandbox keys (with Payouts enabled) and `GUILD_SELLER_ACCOUNTS` (`harbor-audio=…,maya-reviews=…`) on Render.
- Use a fresh sandbox buyer session.
- Configure Claude Desktop with a wallet for its agent.
- If live Claude is on (`GUILD_SHOPPER_LIVE=1`), rehearse once: its attempts vary. The scripted run is reproducible.
