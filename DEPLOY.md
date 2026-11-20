# Deploy BacktestLab securely (Render example, ~10 minutes)

Files to upload to a PRIVATE GitHub repo: server.py, BacktestLab.html, requirements-server.txt, .gitignore, .env.example, tests/
(Do NOT upload any file containing keys. Notebooks with keys must never go in this repo.)

1. Revoke and regenerate any key/token that was ever pasted in chat, email or a notebook (Anthropic key, Dhan token, Telegram, Bybit, Delta).
2. Anthropic Console -> create a NEW key just for this site, in its own Workspace, with a low monthly spend limit (e.g. $5-10).
3. Dhan: generate a token for this site (the Data API plan is needed for historical candles). Data-only use; this server has no order-placing code.
4. Render.com -> New Web Service -> your repo.
   Build: pip install -r requirements-server.txt
   Start: gunicorn server:app --workers 1 --threads 4 --timeout 120
5. Environment tab: add ANTHROPIC_API_KEY, DHAN_ACCESS_TOKEN, DHAN_CLIENT_ID, TRUST_PROXY=1, (optional) ACCESS_CODE, ALLOWED_HOSTS.
6. Open https://<your-app>.onrender.com . Share THAT link - never the HTML file.
7. Dhan tokens expire (typically 24h). When Dhan data shows "token expired", update DHAN_ACCESS_TOKEN in Render. The site keeps working via Yahoo meanwhile.

Run tests before each deploy:  python -m pytest tests -q
Local run:  export the variables, then  python server.py  -> http://127.0.0.1:5000
