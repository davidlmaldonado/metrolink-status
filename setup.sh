#!/bin/bash
set -e

echo "Metrolink Status -- Setup"
echo "========================="
echo ""

if ! command -v python3 &>/dev/null; then
    echo "Python 3 not found. Install: brew install python"
    exit 1
fi
echo "Python: $(python3 --version)"

echo ""
echo "Installing dependencies..."
pip3 install --upgrade rumps requests gtfs-realtime-bindings protobuf

echo ""
echo "Testing alerts feed (no key needed)..."
python3 -c "
import requests
from google.transit import gtfs_realtime_pb2
r = requests.get('https://cdn.simplifytransit.com/metrolink/alerts/service-alerts.pb', timeout=10)
feed = gtfs_realtime_pb2.FeedMessage()
feed.ParseFromString(r.content)
print(f'  Alerts feed OK -- {len(feed.entity)} alerts')
"

CONFIG="$HOME/.config/metrolink_status/config.json"
if [ ! -f "$CONFIG" ]; then
    echo ""
    echo "Config will be created on first run at:"
    echo "  $CONFIG"
    echo ""
    echo "You will need a Metrolink GTFS-RT API key (free):"
    echo "  https://metrolinktrains.com/about/gtfs/gtfs-rt-access/"
fi

echo ""
echo "========================="
echo "Setup complete."
echo ""
echo "Run:  python3 metrolink_status.py"
echo ""
