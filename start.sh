#!/bin/bash
# ─────────────────────────────────────────────────────────
#  GUCCI QUANT — Start everything
#  Usage: bash ~/GucciQuant/start.sh
# ─────────────────────────────────────────────────────────

cd ~/GucciQuant
source venv/bin/activate

echo ""
echo "  ⚡  GUCCI QUANT — Starting up..."
echo ""

# Kill any old sessions quietly
screen -X -S gucci     quit 2>/dev/null; true
screen -X -S dashboard quit 2>/dev/null; true
sleep 1

# Install flask if not already installed
pip install flask -q

# Start bot (detached — won't steal the terminal)
screen -dmS gucci bash -c '
  source ~/GucciQuant/venv/bin/activate
  cd ~/GucciQuant
  python3 main.py
'

# Start dashboard (detached)
screen -dmS dashboard bash -c '
  source ~/GucciQuant/venv/bin/activate
  cd ~/GucciQuant
  python3 dashboard.py
'

sleep 2

echo "  ✅  Bot running"
echo "  ✅  Dashboard running"
echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  Open in browser:                           │"
echo "  │  http://187.124.41.102:8080                 │"
echo "  │                                             │"
echo "  │  View bot logs:  screen -r gucci            │"
echo "  │  View dash logs: screen -r dashboard        │"
echo "  │  All sessions:   screen -ls                 │"
echo "  │  Exit a session: Ctrl+A then D              │"
echo "  └─────────────────────────────────────────────┘"
echo ""
