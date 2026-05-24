================================================================
  BTC OPTIONS BREAKOUT ALGO — Delta Exchange
  Strategy: 10-min confirm | TP=user-defined | SL=50% auto
================================================================

PROJECT STRUCTURE
-----------------
btc_algo/
├── backend/
│   └── main.py          ← Python FastAPI backend
├── frontend/
│   └── index.html       ← Dashboard UI
├── data/
│   └── trades.db        ← Auto-created on first run
├── requirements.txt     ← Python dependencies
└── README.txt           ← This file


STEP 1 — INSTALL PYTHON
------------------------
Make sure Python 3.9+ is installed.
  Windows : Download from https://python.org
  Ubuntu  : sudo apt install python3 python3-pip


STEP 2 — INSTALL DEPENDENCIES
-------------------------------
Open a terminal in the btc_algo folder and run:

  pip install -r requirements.txt


STEP 3 — RUN THE ALGO
----------------------
  cd backend
  python main.py

You will see:
  ✅ Account: Your Name (email@...)
  ✅ Balance synced: USDT 50000.00
  ✅ BTC Options Algo → http://localhost:8000


STEP 4 — OPEN DASHBOARD
-------------------------
  On your PC  :  http://localhost:8000
  On your VPS :  http://YOUR-VPS-IP:8000

(If VPS — open firewall port: sudo ufw allow 8000)


STEP 5 — KEEP RUNNING 24/7 ON VPS (optional)
----------------------------------------------
  screen -S algo
  python main.py
  → Press Ctrl+A then D to detach (keeps running)
  → screen -r algo  to come back


HOW TO USE THE DASHBOARD
--------------------------
1. Strike 1 — Enter strike price (e.g. 76600), type CE/PE, key level
2. Strike 2 — Enter strike price (e.g. 77800), type CE/PE, key level
3. Take Profit — Enter the premium price you want to exit at
4. Press START ALGO

The system will:
• Watch both strikes live for a breakout (>3% move from level)
• Wait 10 minutes to confirm the breakout is genuine
• Buy the OPPOSITE side when confirmed
• Auto-exit when TP hit OR SL hit (50% of entry)
• Log everything in the trade history table


STRATEGY RULES
--------------
• If CALL breaks its level → System BUYS PUT
• If PUT breaks its level  → System BUYS CALL
• Breakout must hold 10 minutes to confirm
• If price comes back inside level during confirmation → NO TRADE
• Only ONE trade open at a time
• SL = 50% of entry price (automatic)
• TP = price you enter in the dashboard


API KEYS (pre-configured in backend/main.py)
---------------------------------------------
API_KEY    = iiu3aJNuen38GAPAbvMccMjrpYDJ6e
API_SECRET = SQp0jPormIyaxUIc1qGf545zt5LNijVZm2R0cL7tekJ6DDZeVtP9PxmY9pA4

To change: open backend/main.py and edit lines 16-17.


MONTHLY RUNNING COST
---------------------
VPS (Contabo/Hostinger) : ₹550 – ₹900/month
Delta API Key           : FREE
Trading Fees            : 0.02% – 0.05% per trade

================================================================
  For support — contact your developer
================================================================
