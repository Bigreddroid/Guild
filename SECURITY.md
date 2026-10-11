# Security

Guild is a hackathon project that moves **PayPal sandbox** money only. The client refuses any PayPal host other than `api-m.sandbox.paypal.com`.

- Keys live in the host's environment settings (Render), never in the repository. See `render.yaml` for the variable names.
- To report a problem, open a GitHub issue without secrets or personal data. For anything sensitive, contact the repository owner through their GitHub profile.

The design choices that limit damage are listed under **Safety rails** in the [README](README.md#safety-rails).
