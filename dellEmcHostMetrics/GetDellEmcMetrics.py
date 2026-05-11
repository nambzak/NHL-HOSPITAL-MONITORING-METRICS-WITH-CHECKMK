#!/usr/bin/env python3
"""
Dell EMC PowerVault ME5024 — Checkmk Local Check
Fetches each object via its individual URL for full detail.
"""

import requests, hashlib, sys, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── CONFIGURATION ─────────────────────────────
HOST     = ""
USERNAME = ""
PASSWORD = ""
BASE_URL = f"https://{HOST}/rest/v1"
# ─────────────────────────────────────────────

SESSION_KEY = None

def login():
    global SESSION_KEY
    h = hashlib.sha256(f"{USERNAME}_{PASSWORD}".encode()).hexdigest()
    r = requests.get(f"{BASE_URL}/login/{h}",
                     headers={"dataType": "json"},
                     verify=False, timeout=15)
    SESSION_KEY = r.json()["status"][0]["response"]

def get(url_or_path):
    """Fetch a full URL or a /rest/v1/... path."""
    url = url_or_path if url_or_path.startswith("https://") else f"{BASE_URL}/{url_or_path}"
    r = requests.get(url,
                     headers={"dataType": "json", "sessionKey": SESSION_KEY},
                     verify=False, timeout=15)
    return r.json()

def get_list(endpoint):
    """Return list of summary objects from a collection endpoint."""
    data = get(endpoint)
    for k, v in data.items():
        if isinstance(v, list) and k not in ("status",):
            return v
    return []

def fetch_item(summary_obj):
    """Fetch the full detail object using the 'url' field."""
    url = summary_obj.get("url")
    if not url:
        return summary_obj
    data = get(url)
    for k, v in data.items():
        if isinstance(v, list) and k not in ("status",) and v:
            return v[0]
    return summary_obj

def health_state(health):
    h = str(health).lower()
    if h in ["ok","good","informational","operational","up","online","linear","virtual"]:
        return 0
    if h in ["degraded","warning","marginal","unrecovered"]:
        return 1
    if h in ["fault","failed","error","critical","damaged","unknown","not-installed","down","offline"]:
        return 2
    return 3

def out(state, service, summary):
    print(f'{state} "PowerVault {service}" - {summary}')


# ── Checks ─────────────────────────────────────

def check_system():
    try:
        data = get("system")
        si   = data.get("system", [{}])[0]
        health  = si.get("health", "unknown")
        reason  = si.get("health-reason", "")
        model   = si.get("product-id", "ME5024")
        name    = si.get("system-name", "Storage Array")
        fw      = si.get("bundle-version", si.get("sc-fw", "?"))
        state   = health_state(health)
        summary = f"Health: {health} | Model: {model} | FW: {fw} | Name: {name}"
        if reason: summary += f" | Reason: {reason}"
        out(state, "System Health", summary)
    except Exception as e:
        out(3, "System Health", f"ERROR: {e}")


def check_controllers():
    try:
        for s in get_list("controllers"):
            item    = fetch_item(s)
            cid     = item.get("controller-id", "?")
            health  = item.get("health", "unknown")
            status  = item.get("status", "unknown")
            ip      = item.get("ip-address", "?")
            fw      = item.get("sc-fw", item.get("bundle-version", "?"))
            state   = health_state(health)
            summary = f"Status: {status} | Health: {health} | IP: {ip} | FW: {fw}"
            out(state, f"Controller {cid}", summary)
    except Exception as e:
        out(3, "Controllers", f"ERROR: {e}")


def check_disks():
    try:
        summaries = get_list("drives")
        ok_c = warn_c = crit_c = 0
        crit_d = []; warn_d = []; all_items = []

        for s in summaries:
            item   = fetch_item(s)
            loc    = item.get("location", "?")
            health = item.get("health", "unknown")
            status = item.get("status", "unknown")
            state  = health_state(health)
            all_items.append((item, state))
            if state == 0:   ok_c += 1
            elif state == 1: warn_c += 1; warn_d.append(f"{loc}({status})")
            elif state == 2: crit_c += 1; crit_d.append(f"{loc}({status})")

        total = len(summaries)
        if crit_c > 0:
            out(2, "Physical Disks", f"{ok_c}/{total} OK | {crit_c} FAILED: {', '.join(crit_d)}")
        elif warn_c > 0:
            out(1, "Physical Disks", f"{ok_c}/{total} OK | {warn_c} DEGRADED: {', '.join(warn_d)}")
        else:
            out(0, "Physical Disks", f"All {total} disks healthy")

        for item, state in all_items:
            if state > 0:
                loc    = item.get("location","?")
                health = item.get("health","unknown")
                status = item.get("status","unknown")
                vendor = item.get("vendor","?")
                model  = item.get("model","?")
                size   = item.get("size", item.get("formatted-size","?"))
                out(state, f"Disk {loc}", f"Status: {status} | Health: {health} | {vendor} {model} {size}")
    except Exception as e:
        out(3, "Physical Disks", f"ERROR: {e}")


def check_disk_groups():
    try:
        summaries = get_list("disk-groups")
        if not summaries:
            out(0, "Disk Groups", "No disk groups configured"); return
        for s in summaries:
            item    = fetch_item(s)
            name    = item.get("name", "?")
            health  = item.get("health", "unknown")
            status  = item.get("status", "unknown")
            raid    = item.get("raidtype", item.get("raid", "?"))
            size    = item.get("size", item.get("total-size", "?"))
            free    = item.get("freespace", item.get("free-size", "?"))
            state   = health_state(health)
            summary = f"RAID: {raid} | Status: {status} | Health: {health} | Size: {size} | Free: {free}"
            out(state, f"Disk Group {name}", summary)
    except Exception as e:
        out(3, "Disk Groups", f"ERROR: {e}")


def check_volumes():
    try:
        summaries = get_list("volumes")
        if not summaries:
            out(0, "Volumes", "No volumes configured"); return
        for s in summaries:
            item   = fetch_item(s)
            name   = item.get("volume-name", item.get("name", "?"))
            health = item.get("health", "unknown")
            size   = item.get("total-size", item.get("size", "?"))
            owner  = item.get("owner", "?")
            state  = health_state(health)
            out(state, f"Volume {name}", f"Health: {health} | Size: {size} | Owner: Controller {owner}")
    except Exception as e:
        out(3, "Volumes", f"ERROR: {e}")


def check_enclosures():
    try:
        for s in get_list("enclosures"):
            item   = fetch_item(s)
            eid    = item.get("enclosure-id", "?")
            health = item.get("health", "unknown")
            status = item.get("status", "unknown")
            model  = item.get("model", "?")
            state  = health_state(health)
            out(state, f"Enclosure {eid}", f"Health: {health} | Status: {status} | Model: {model}")
    except Exception as e:
        out(3, "Enclosures", f"ERROR: {e}")


def check_fans():
    try:
        summaries = get_list("fans")
        ok_c = warn_c = crit_c = 0; problems = []
        for s in summaries:
            item   = fetch_item(s)
            name   = item.get("name", item.get("location", item.get("durable-id", "?")))
            health = item.get("health", "unknown")
            status = item.get("status", "unknown")
            speed  = item.get("speed", "")
            state  = health_state(health)
            if state == 0:   ok_c += 1
            elif state == 1: warn_c += 1; problems.append(f"{name}:{status}")
            elif state == 2: crit_c += 1; problems.append(f"{name}:FAILED")
        total = len(summaries)
        if crit_c > 0:
            out(2, "Fans", f"{ok_c}/{total} OK | {crit_c} FAILED: {', '.join(problems)}")
        elif warn_c > 0:
            out(1, "Fans", f"{ok_c}/{total} OK | {warn_c} WARNING: {', '.join(problems)}")
        else:
            out(0, "Fans", f"All {total} fans OK")
    except Exception as e:
        out(3, "Fans", f"ERROR: {e}")


def check_psus():
    try:
        for s in get_list("power-supplies"):
            item   = fetch_item(s)
            name   = item.get("name", item.get("location", item.get("durable-id", "?")))
            health = item.get("health", "unknown")
            status = item.get("status", "unknown")
            state  = health_state(health)
            out(state, f"PSU {name}", f"Status: {status} | Health: {health}")
    except Exception as e:
        out(3, "Power Supplies", f"ERROR: {e}")


def check_sensors():
    try:
        data    = get("sensors")
        sensors = data.get("sensors", data.get("sensor", []))
        ok_c = 0; warn_s = []; crit_s = []
        for s in sensors:
            sname   = s.get("sensor-name", s.get("name", "?"))
            status  = s.get("status", s.get("health", "unknown"))
            reading = s.get("reading", "?")
            state   = health_state(status)
            if state == 0:   ok_c += 1
            elif state == 1: warn_s.append(f"{sname}:{reading}")
            elif state == 2: crit_s.append(f"{sname}:{reading}")
        total = len(sensors)
        if crit_s:
            out(2, "Sensors", f"{ok_c}/{total} OK | CRIT: {', '.join(crit_s)}")
        elif warn_s:
            out(1, "Sensors", f"{ok_c}/{total} OK | WARN: {', '.join(warn_s)}")
        else:
            out(0, "Sensors", f"All {total} sensors normal")
    except Exception as e:
        out(3, "Sensors", f"ERROR: {e}")


def check_ports():
    try:
        summaries = get_list("ports")
        ok_c = down_c = 0; down_p = []
        for s in summaries:
            item   = fetch_item(s)
            pname  = item.get("port", item.get("name", "?"))
            health = item.get("health", "unknown")
            status = item.get("status", "unknown")
            ptype  = item.get("port-type", item.get("type", "?"))
            speed  = item.get("actual-speed", "")
            state  = health_state(health)
            if state == 0: ok_c += 1
            else:          down_c += 1; down_p.append(f"{pname}({ptype}:{status})")
        total = len(summaries)
        if down_c > 0:
            out(1, "Ports", f"{ok_c}/{total} UP | DOWN: {', '.join(down_p)}")
        else:
            out(0, "Ports", f"All {total} ports UP")
    except Exception as e:
        out(3, "Ports", f"ERROR: {e}")


if __name__ == "__main__":
    try:
        login()
    except Exception as e:
        print(f'2 "PowerVault Login" - CRIT: Cannot connect: {e}')
        sys.exit(0)

    check_system()
    check_controllers()
    check_disks()
    check_disk_groups()
    check_volumes()
    check_enclosures()
    check_fans()
    check_psus()
    check_sensors()
    check_ports()
