
# Combined Infrastructure Daily Report

A comprehensive automated daily report merging Checkmk host monitoring data
with Dell EMC PowerVault ME5024 storage array health metrics. Generates both
HTML email and PDF output for complete infrastructure visibility.

**Author:** Buyinga Isaac Nambafu  
**Role:** ICT Infrastructure Officer  
**Organization:** Nakasero Hospital

---

## Overview

This all-in-one script combines host-level metrics from Checkmk with storage-level
health data from the Dell EMC PowerVault ME5024. It produces a richly formatted
report with graphs, tables, and health summaries, delivered via email at 7:00 AM
daily.

---

## Features

### Checkmk Host Monitoring
- Connects to Livestatus Unix socket for real-time data
- CPU utilization, memory, disk usage, and uptime metrics
- VMware vSphere-specific metric parsing for ESXi hosts
- Datastore usage analysis for all filesystem services
- Configurable host exclusion

### Dell EMC PowerVault Monitoring
- REST API integration with session-based authentication
- Monitors: System, Controllers, Disks, Disk Groups, Volumes, Fans, PSUs, Ports
- Health classification across all components
- Disk health visualization

### Graphs & Charts
- Per-host resource usage horizontal bar charts (CPU, Memory, Disk)
- Per-host datastore usage charts
- Host state overview donut chart
- Top 12 high-usage hosts grouped bar chart
- EMC disk health summary chart

### PDF Report (Multi-Page A4)
| Page | Content |
|------|---------|
| 1 | Title, summary cards, overview charts, host summary table |
| 2 | Per-host detail with graphs and datastore tables |
| 3 | Dell EMC storage array complete health report |

### HTML Email
- Responsive design with embedded base64 images
- Color-coded metric badges and status indicators
- System health banner with quick stats
- Direct link to live Checkmk dashboard

---

## Configuration

| Setting | Description |
|---------|-------------|
| `LIVESTATUS_SOCKET` | Checkmk Livestatus socket path |
| `CHECKMK_URL` | Checkmk instance URL |
| `EMC_HOST` | Dell EMC PowerVault IP address |
| `EMC_USERNAME` | API username |
| `EMC_PASSWORD` | API password |
| `SMTP_SERVER` | Zoho SMTP server |
| `SMTP_PORT` | SMTP port (465) |
| `SMTP_USER` | Sender email |
| `SMTP_PASSWORD` | Email password |
| `RECIPIENTS` | Email distribution list |
| `SKIP_HOSTS` | Hosts to exclude |
| `VSPHERE_HOSTS` | VMware hosts |

---

## Dependencies
matplotlib>=3.0
reportlab>=3.5
numpy>=1.18
requests>=2.20
urllib3


---

## Output

| File | Description |
|------|-------------|
| `/tmp/nakasero_daily_report.pdf` | Generated PDF report |
| `/tmp/nakasero_graphs/` | Directory with all PNG graphs |

---

## Usage

### Cron Job (Scheduled)
0 7 * * * python3 /opt/checkmk_daily_report.py >> /var/log/nakasero_report.log 2>&1

Runs daily at 7:00 AM EAT (Kampala, Uganda).

### Manual Execution
```bash
python3 /opt/checkmk_daily_report.py

Report Structure
Header — Nakasero Hospital branding with timestamp

State Summary — Donut chart and count cards

Alert Chart — High resource usage hosts (≥75%)

Host Table — All hosts with status and metrics

Per-Host Detail — Individual graphs and datastore info

Dell EMC Section — System, controllers, disks, volumes, environmental

Footer — Auto-generation notice and dashboard link

Severity Thresholds
Usage	Color	Indication
< 50%	Green	Healthy
50–74%	Yellow	Moderate
75–89%	Orange	Warning
≥ 90%	Red	Critical

License
Proprietary — Nakasero Hospital ICT Department. All rights reserved.
