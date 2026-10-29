#!/bin/bash
set -e
python -m venv venv
source venv/bin/activate || source venv/Scripts/activate
pip install -r requirements.txt
cp -n config/config.example.yaml config/config.yaml || true
echo "Setup complete. Edit config/config.yaml then run:"
echo "  python main.py seed --cash 100000"
echo "  python main.py signal"
echo "  python main.py dashboard --port 5050"
