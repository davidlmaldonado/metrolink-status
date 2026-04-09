#!/usr/bin/env python3
"""
Metrolink Status — Real-time Metrolink train status in your macOS menu bar.

Shows upcoming departures, delays, and service alerts for your configured
stations using the official Metrolink GTFS-RT protobuf feeds.

Requirements:
    pip install rumps requests gtfs-realtime-bindings protobuf

Usage:
    python3 metrolink_status.py

Config:  ~/.config/metrolink_status/config.json  (auto-created on first run)
Logs:    ~/.config/metrolink_status/metrolink_status.log
"""

import json
import logging
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

try:
    import rumps
except ImportError:
    sys.exit("Missing: pip install rumps")

try:
    import requests
except ImportError:
    sys.exit("Missing: pip install requests")

try:
    from google.transit import gtfs_realtime_pb2
except ImportError:
    sys.exit("Missing: pip install gtfs-realtime-bindings protobuf")


# ── Paths ────────────────────────────────────────────────────────────────────

APP_NAME = "Metrolink Status"
CONFIG_DIR = Path.home() / ".config" / "metrolink_status"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "metrolink_status.log"

# ── Metrolink GTFS-RT endpoints ──────────────────────────────────────────────

TRIPS_URL = "https://metrolink-gtfsrt.gbsdigital.us/feed/gtfsrt-trips"
VEHICLES_URL = "https://metrolink-gtfsrt.gbsdigital.us/feed/gtfsrt-vehicles"
ALERTS_URL = "https://cdn.simplifytransit.com/metrolink/alerts/service-alerts.pb"

# ── Stop ID → name mapping (from Metrolink GTFS static feed) ────────────────

STOP_NAMES = {
    "101": "Baldwin Park",
    "102": "Burbank - Downtown",
    "103": "Chatsworth",
    "104": "Covina",
    "105": "El Monte",
    "106": "Glendale",
    "107": "L.A. Union Station",
    "108": "Moorpark",
    "109": "Pomona - North",
    "110": "Sylmar / San Fernando",
    "111": "Santa Clarita",
    "112": "Simi Valley",
    "113": "Van Nuys",
    "114": "Claremont",
    "115": "Cal State LA",
    "116": "Fontana",
    "117": "Ontario - East",
    "118": "Montclair",
    "119": "Jurupa Valley / Pedley",
    "120": "Pomona - Downtown",
    "121": "Rancho Cucamonga",
    "122": "Rialto",
    "123": "Riverside - Downtown",
    "124": "San Bernardino Depot",
    "125": "Upland",
    "126": "Montebello / Commerce",
    "127": "Industry",
    "128": "Anaheim - ARTIC",
    "129": "Sun Valley",
    "130": "Buena Park",
    "133": "San Clemente Pier",
    "135": "Commerce",
    "140": "Irvine",
    "141": "Laguna Niguel / Mission Viejo",
    "143": "Norwalk / Santa Fe Springs",
    "144": "Oceanside",
    "145": "Orange",
    "147": "Corona - North Main",
    "148": "Riverside - La Sierra",
    "152": "San Clemente",
    "153": "San Juan Capistrano",
    "154": "Santa Ana",
    "156": "Tustin",
    "161": "Vista Canyon",
    "162": "Lancaster",
    "163": "Palmdale",
    "164": "Via Princessa",
    "165": "Vincent Grade / Acton",
    "166": "Camarillo",
    "167": "Northridge",
    "168": "Oxnard",
    "169": "Ventura - East",
    "170": "Burbank Airport - South",
    "171": "Anaheim Canyon",
    "173": "Corona - West",
    "174": "Fullerton",
    "175": "Newhall",
    "181": "Riverside - Hunter Park / UCR",
    "182": "Moreno Valley / March Field",
    "183": "Perris - Downtown",
    "184": "Perris - South",
    "185": "San Bernardino - Downtown",
    "186": "Burbank Airport - North",
    "188": "San Bernardino - Tippecanoe",
    "189": "Redlands - Esri",
    "190": "Redlands - Downtown",
    "191": "Redlands - University",
}

# ── Route short names ────────────────────────────────────────────────────────

ROUTE_SHORT = {
    "Antelope Valley Line": "AV",
    "Ventura County Line": "VC",
    "San Bernardino Line": "SB",
    "Riverside Line": "RIV",
    "Orange County Line": "OC",
    "Inland Emp.-Orange Co. Line": "IEOC",
    "91 Line": "91",
    "91/Perris Valley Line": "91",
    "Arrow": "ARW",
}

# ── Default config ───────────────────────────────────────────────────────────
# GitHub-friendly defaults: Burbank - Downtown <-> L.A. Union Station
# For personal use, swap in your own stations and routes.

DEFAULT_CONFIG = {
    "api_key": "",
    "stations": [
        {
            "name": "Burbank - Downtown",
            "stop_id": "102",
            "routes": ["Antelope Valley Line", "Ventura County Line"],
        },
        {
            "name": "L.A. Union Station",
            "stop_id": "107",
            "routes": [],
        },
    ],
    "poll_interval_seconds": 120,
    "active_hours": {
        "morning": {"start": "05:00", "end": "10:30"},
        "evening": {"start": "14:00", "end": "20:30"},
    },
    "always_active": False,
    "menu_bar_station": 0,
    "menu_bar_format": "compact",
    "show_alerts": True,
    "max_departures": 6,
}


# ── Logging ──────────────────────────────────────────────────────────────────

def setup_logging():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
    )


def log(msg):
    logging.info(msg)


# ── Config ───────────────────────────────────────────────────────────────────

def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        log(f"Created config at {CONFIG_FILE}")
        return None  # Signal: needs API key
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        if not cfg.get("api_key"):
            return None
        return cfg
    except Exception as e:
        log(f"Config error: {e}")
        return None


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        log(f"Config save error: {e}")


# ── Protobuf feed fetchers ──────────────────────────────────────────────────

def fetch_trips_pb(api_key, timeout=15):
    """Fetch GTFS-RT TripUpdates protobuf feed. Returns FeedMessage or None."""
    try:
        r = requests.get(
            TRIPS_URL,
            headers={"X-Api-Key": api_key},
            timeout=timeout,
        )
        r.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(r.content)
        return feed
    except Exception as e:
        log(f"Trips feed error: {e}")
        return None


def fetch_alerts_pb(timeout=10):
    """Fetch GTFS-RT Alerts protobuf feed. No key required."""
    try:
        r = requests.get(ALERTS_URL, timeout=timeout)
        r.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(r.content)
        return feed
    except Exception as e:
        log(f"Alerts feed error: {e}")
        return None


# ── Data parsing ─────────────────────────────────────────────────────────────

def parse_departures(feed, target_stop_id, route_filter=None, max_results=6):
    """
    Extract upcoming departures for a stop from a TripUpdates feed.

    Returns sorted list:
      { train, route, route_short, direction, stop_id,
        scheduled, delay_sec, estimated, headsign }
    """
    if feed is None:
        return []

    results = []
    now = datetime.now()
    target = str(target_stop_id)

    for entity in feed.entity:
        tu = entity.trip_update
        if not tu:
            continue

        route = tu.trip.route_id
        if route_filter and route not in route_filter:
            continue

        train = tu.vehicle.label or tu.trip.trip_id or "?"
        direction = tu.trip.direction_id  # 0=inbound, 1=outbound
        global_delay = tu.delay or 0

        # Determine headsign from last stop in the sequence
        headsign = ""
        if tu.stop_time_update:
            last_stop_id = str(tu.stop_time_update[-1].stop_id)
            headsign = STOP_NAMES.get(last_stop_id, last_stop_id)

        for stu in tu.stop_time_update:
            if str(stu.stop_id) != target:
                continue

            dep = stu.departure if stu.departure.time else stu.arrival
            if not dep.time:
                continue

            delay_sec = dep.delay or global_delay
            estimated = datetime.fromtimestamp(dep.time)
            scheduled = datetime.fromtimestamp(dep.time - delay_sec)

            # Skip departed (2 min grace)
            if estimated < now - timedelta(minutes=2):
                continue

            results.append({
                "train": train,
                "route": route,
                "route_short": ROUTE_SHORT.get(route, route[:4]),
                "direction": direction,
                "stop_id": target,
                "scheduled": scheduled,
                "delay_sec": delay_sec,
                "estimated": estimated,
                "headsign": headsign,
            })
            break  # One match per trip

    results.sort(key=lambda d: d["estimated"])
    return results[:max_results]


def parse_alerts(feed, route_filter=None):
    """Extract alerts, optionally filtered to specific routes."""
    if feed is None:
        return []

    alerts = []
    for entity in feed.entity:
        a = entity.alert
        if not a:
            continue

        # Check route relevance
        if route_filter:
            informed_routes = [ie.route_id for ie in a.informed_entity if ie.route_id]
            if informed_routes and not any(r in route_filter for r in informed_routes):
                continue

        header = ""
        if a.header_text.translation:
            header = a.header_text.translation[0].text
        desc = ""
        if a.description_text.translation:
            desc = a.description_text.translation[0].text
        url = ""
        if a.url.translation:
            url = a.url.translation[0].text

        if header or desc:
            alerts.append({"header": header, "description": desc, "url": url})

    return alerts


# ── Time / display helpers ───────────────────────────────────────────────────

def is_active(hours, always=False):
    if always:
        return True
    now = datetime.now()
    for _, w in hours.items():
        sh, sm = map(int, w["start"].split(":"))
        eh, em = map(int, w["end"].split(":"))
        s = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        e = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        if s <= now <= e:
            return True
    return False


def fmt_mins(dt):
    """Minutes from now as compact string."""
    m = int((dt - datetime.now()).total_seconds() / 60)
    if m <= 0:
        return "now"
    if m < 60:
        return f"{m}m"
    h, r = divmod(m, 60)
    return f"{h}h{r:02d}m" if r else f"{h}h"


def fmt_time(dt):
    """12-hour time like 3:33p."""
    return dt.strftime("%-I:%M%p").lower().replace("am", "a").replace("pm", "p")


def delay_label(sec):
    """Compact delay string for dropdown."""
    if abs(sec) < 30:
        return ""
    m = sec // 60
    if m > 0:
        return f"+{m}m"
    return f"{abs(m)}m early"


def delay_dots(sec):
    """Severity dot meter matching commute_eta design."""
    if abs(sec) < 60:
        return ""          # on time — no dots needed
    if sec < 300:
        return " *"        # minor (<5m)
    if sec < 600:
        return " **"       # moderate (<10m)
    return " ***"          # severe (10m+)


def status_char(sec):
    """Single-char status for dropdown lines. Monochrome Unicode."""
    if abs(sec) < 60:
        return "\u2022"    # bullet: on time
    if sec < 300:
        return "\u2023"    # triangle bullet: minor delay
    if sec < 600:
        return "\u25B8"    # right triangle: moderate
    return "\u25C6"        # diamond: severe


# ── Menu Bar App ─────────────────────────────────────────────────────────────

class MetrolinkStatus(rumps.App):
    def __init__(self):
        super().__init__("--", quit_button=None)

        self.config = load_config()

        if self.config is None:
            self.title = "ML: no key"
            self.menu.add(rumps.MenuItem(
                "API key needed -- click to open config",
                callback=self._open_cfg,
            ))
            self.menu.add(rumps.MenuItem(
                "Get a key: metrolinktrains.com/about/gtfs/gtfs-rt-access",
                callback=self._open_signup,
            ))
            self.menu.add(None)
            self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))
            return

        self.api_key = self.config["api_key"]
        self.stations = self.config.get("stations", [])
        self.poll_interval = self.config.get("poll_interval_seconds", 120)
        self.active_hours = self.config.get("active_hours", {})
        self.always_on = self.config.get("always_active", False)
        self.mb_idx = self.config.get("menu_bar_station", 0)
        self.mb_fmt = self.config.get("menu_bar_format", "compact")
        self.show_alerts_cfg = self.config.get("show_alerts", True)
        self.max_deps = self.config.get("max_departures", 6)

        self.station_data = {}
        self.alert_data = []
        self.last_update = None
        self.is_sleeping = False
        self.is_paused = False

        # Direct references to menu items
        self.dep_items = []
        self.alert_items = []
        self.station_headers = []

        self._build_menu()
        self._start_poll()

    # ── Menu construction ────────────────────────────────────────────────

    def _build_menu(self):
        for i, st in enumerate(self.stations):
            name = st["name"]
            hdr = rumps.MenuItem(name, callback=self._mk_station_cb(i))
            hdr.state = (i == self.mb_idx)
            self.station_headers.append(hdr)
            self.menu.add(hdr)

            items = []
            for j in range(self.max_deps):
                mi = rumps.MenuItem("  loading..." if j == 0 else "")
                items.append(mi)
                self.menu.add(mi)
            self.dep_items.append(items)
            self.menu.add(None)

        if self.show_alerts_cfg:
            self.alerts_hdr = rumps.MenuItem("Alerts")
            self.menu.add(self.alerts_hdr)
            self.alert_items = []
            for _ in range(4):
                mi = rumps.MenuItem("")
                self.alert_items.append(mi)
                self.menu.add(mi)
            self.menu.add(None)

        self.status_mi = rumps.MenuItem("Updated: --")
        self.menu.add(self.status_mi)
        self.sched_mi = rumps.MenuItem("")
        self.menu.add(self.sched_mi)
        self.menu.add(None)

        self.toggle_mi = rumps.MenuItem("|| Pause", callback=self._toggle)
        self.menu.add(self.toggle_mi)
        self.menu.add(rumps.MenuItem("Refresh Now", callback=self._refresh))
        self.menu.add(rumps.MenuItem("Open Config", callback=self._open_cfg))
        self.menu.add(rumps.MenuItem("Train Status Page", callback=self._open_web))
        self.menu.add(None)
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    def _mk_station_cb(self, idx):
        def cb(_):
            self.mb_idx = idx
            self.config["menu_bar_station"] = idx
            for i, h in enumerate(self.station_headers):
                h.state = (i == idx)
            self._update_title()
            save_config(self.config)
        return cb

    # ── Polling ──────────────────────────────────────────────────────────

    def _start_poll(self):
        self._elapsed = self.poll_interval
        self._timer = rumps.Timer(self._tick, 30)
        self._timer.start()
        self._tick(None)

    def _tick(self, _):
        if self.is_paused:
            return

        if not is_active(self.active_hours, self.always_on):
            if not self.is_sleeping:
                self.is_sleeping = True
                self.title = "--"
                self.sched_mi.title = "Outside active hours"
                log("Sleeping")
            return

        if self.is_sleeping:
            self.is_sleeping = False
            self.sched_mi.title = ""
            self._elapsed = self.poll_interval
            log("Waking")

        self._elapsed += 30
        if self._elapsed >= self.poll_interval:
            self._elapsed = 0
            threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        log("Fetching trips...")
        feed = fetch_trips_pb(self.api_key)
        if feed:
            log(f"Got {len(feed.entity)} trip entities")
        else:
            log("Feed returned None")

        # Collect all routes across stations for alert filtering
        all_routes = set()

        for i, st in enumerate(self.stations):
            stop_id = st["stop_id"]
            route_filter = st.get("routes") or None
            if route_filter:
                all_routes.update(route_filter)

            deps = parse_departures(
                feed, stop_id, route_filter, self.max_deps
            )
            self.station_data[st["name"]] = deps

        if self.show_alerts_cfg:
            afeed = fetch_alerts_pb()
            self.alert_data = parse_alerts(
                afeed, all_routes if all_routes else None
            )[:4]

        self.last_update = datetime.now()
        self._update_menu()
        self._update_title()

    # ── Display updates ──────────────────────────────────────────────────

    def _update_menu(self):
        for i, st in enumerate(self.stations):
            deps = self.station_data.get(st["name"], [])
            for j, mi in enumerate(self.dep_items[i]):
                if j < len(deps):
                    d = deps[j]
                    sc = status_char(d["delay_sec"])
                    rs = d["route_short"]
                    tr = d["train"]
                    et = fmt_time(d["estimated"])
                    mn = fmt_mins(d["estimated"])
                    dl = delay_label(d["delay_sec"])
                    hs = d["headsign"]

                    # Format: "  . AV 227  3:33p (12m)  > Lancaster"
                    line = f"  {sc} {rs} {tr}  {et} ({mn})"
                    if dl:
                        line += f"  {dl}"
                    if hs:
                        line += f"  > {hs}"
                    mi.title = line
                elif j == 0 and not deps:
                    mi.title = "  No upcoming departures"
                else:
                    mi.title = ""

        if self.show_alerts_cfg:
            for j, mi in enumerate(self.alert_items):
                if j < len(self.alert_data):
                    h = self.alert_data[j]["header"]
                    # Truncate for menu readability
                    if len(h) > 70:
                        h = h[:67] + "..."
                    mi.title = f"  {h}" if h else ""
                else:
                    mi.title = ""

            n = len(self.alert_data)
            self.alerts_hdr.title = f"Alerts ({n})" if n else "No alerts"

        if self.last_update:
            self.status_mi.title = f"Updated {fmt_time(self.last_update)}"

    def _update_title(self):
        if self.is_sleeping or self.is_paused:
            return

        if self.mb_idx >= len(self.stations):
            self.title = "--"
            return

        name = self.stations[self.mb_idx]["name"]
        deps = self.station_data.get(name, [])

        if not deps:
            self.title = "--"
            return

        d = deps[0]
        mn = fmt_mins(d["estimated"])
        dl = delay_dots(d["delay_sec"])

        if self.mb_fmt == "full":
            rs = d["route_short"]
            tr = d["train"]
            self.title = f"{rs}{tr} {mn}{dl}"
        else:
            self.title = f"{mn}{dl}"

    # ── Actions ──────────────────────────────────────────────────────────

    def _toggle(self, _):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.toggle_mi.title = "> Resume"
            self.title = "||"
            log("Paused")
        else:
            self.toggle_mi.title = "|| Pause"
            self._elapsed = self.poll_interval
            log("Resumed")

    def _refresh(self, _):
        self.title = "..."
        threading.Thread(target=self._fetch, daemon=True).start()

    def _open_cfg(self, _):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.exists():
            with open(CONFIG_FILE, "w") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
        subprocess.run(["open", str(CONFIG_FILE)])

    def _open_web(self, _):
        subprocess.run(["open", "https://metrolinktrains.com/train_status/"])

    def _open_signup(self, _):
        subprocess.run([
            "open",
            "https://metrolinktrains.com/about/gtfs/gtfs-rt-access/",
        ])


# ── Entry ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    setup_logging()
    log(f"{APP_NAME} starting")
    app = MetrolinkStatus()
    app.run()
