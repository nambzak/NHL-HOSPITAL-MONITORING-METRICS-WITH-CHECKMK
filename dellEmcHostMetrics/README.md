# Dell EMC PowerVault ME5024 — Checkmk Local Check

A Checkmk local check plugin that monitors the health and status of a
Dell EMC PowerVault ME5024 storage array via its REST API.

**Author:** Buyinga Isaac Nambafu  
**Role:** ICT Infrastructure Officer  
**Organization:** Nakasero Hospital

---

## Overview

This script authenticates to the Dell EMC PowerVault ME5024 REST API and fetches
detailed health information for all major components. Results are output in
Checkmk-compatible local check format for seamless integration into the monitoring
dashboard.

---

## Features

- SHA-256 hashed API authentication
- Fetches detailed component data by resolving individual object URLs
- Monitors the following components:

| Component | Details Checked |
|-----------|----------------|
| System Health | Overall health, model, firmware version |
| Controllers | Status, health, IP address, firmware |
| Physical Disks | Health, status, vendor, model, size |
| Disk Groups | RAID type, health, size, free space |
| Volumes | Health, size, controller ownership |
| Enclosures | Health, status, model |
| Fans | Health, status, aggregate reporting |
| Power Supplies | Status, health |
| Sensors | Environmental readings |
| Ports | Status, health, port type |

---

## Health State Mapping

| State | Checkmk Code | Description |
|-------|-------------|-------------|
| OK, Good, Operational, Up, Online | 0 | Healthy |
| Degraded, Warning, Marginal | 1 | Warning |
| Fault, Failed, Error, Critical, Down | 2 | Critical |
| Unknown, Other | 3 | Unknown |

---

## Configuration

| Setting | Description |
|---------|-------------|
| `HOST` | IP address of Dell EMC PowerVault array |
| `USERNAME` | API username |
| `PASSWORD` | API password |
| `BASE_URL` | REST API base URL |
requests>=2.20
urllib3

---

## Dependencies
requests>=2.20
urllib3

---

## Output Format

The script outputs in standard Checkmk local check format:
0 "PowerVault System Health" - Health: OK | Model: ME5024 | FW: ...
0 "PowerVault Controller A" - Status: Operational | Health: OK | IP: ...
0 "PowerVault Physical Disks" - All 12 disks healthy
0 "PowerVault Disk Group dg01" - RAID: RAID5 | Status: OK | Health: OK | ...
0 "PowerVault Volume vol01" - Health: OK | Size: 2TB | Owner: Controller A
0 "PowerVault Fans" - All 4 fans OK
0 "PowerVault PSU PSU-1" - Status: Operational | Health: OK
0 "PowerVault Ports" - All 8 ports UP

---

## Deployment

Place this script in the appropriate Checkmk local checks directory:
omd/sites/<sitename>/local/lib/check_mk/base/plugins/agent_based/


Or execute directly on the Checkmk server for testing.

---

## Usage

### Manual Execution
```bash
python3 /opt/checkmk_emc_powervault.py

License
Proprietary — Nakasero Hospital ICT Department. All rights reserved.
