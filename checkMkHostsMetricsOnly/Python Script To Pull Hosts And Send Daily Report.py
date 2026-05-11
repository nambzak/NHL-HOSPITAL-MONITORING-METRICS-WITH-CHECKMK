#!/usr/bin/env python3
"""
=============================================================
  Nakasero Hospital IT — Daily Infrastructure Report
  Covers: All Checkmk hosts + Dell EMC PowerVault ME5024
  Sends HTML email + PDF at 7:00 AM EAT (Kampala)
=============================================================
"""

import socket as unixsock
import smtplib, ssl, datetime, sys, os, base64, json, re
import hashlib, requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from email.mime.base      import MIMEBase
from email                import encoders

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
    MPL_OK = True
except ImportError:
    MPL_OK = False

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib           import colors
    from reportlab.platypus      import (SimpleDocTemplate, Table, TableStyle,
                                         Paragraph, Spacer, HRFlowable,
                                         Image, KeepTogether, PageBreak)
    from reportlab.lib.styles    import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units     import cm
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False


# ═══════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════
LIVESTATUS_SOCKET = "/omd/sites/nhlsite/tmp/run/live"
CHECKMK_URL       = "http://192.168.10.203/nhlsite"

EMC_HOST     = ""
EMC_USERNAME = ""
EMC_PASSWORD = ""
EMC_BASE_URL = f"https://{EMC_HOST}/rest/v1"

SMTP_SERVER   = ""
SMTP_PORT     = 
SMTP_USER     = ""
SMTP_PASSWORD = ""
SMTP_FROM     = "Nakasero Hospital Monitoring <isaac.nambafu@nakaserohospital.com>"

RECIPIENTS = [
    "nhlitinternal@nakaserohospital.com",
    "gerald.balitwawula@nhl.co.ug",
    "sydney.nahamya@nakaserohospital.com",
    "isaacnbfu@gmail.com",
]

PDF_PATH  = "/tmp/nakasero_daily_report.pdf"
GRAPH_DIR = "/tmp/nakasero_graphs"
SKIP_HOSTS    = {"RIS-AIO"}
VSPHERE_HOSTS = {"NHL-VSPHERE", "ESXI-HOST1", "ESXI-HOST2"}
# ═══════════════════════════════════════════════════════════


# ╔══════════════════════════════════════════════════════════╗
# ║  SECTION 1 — CHECKMK HOST DATA                          ║
# ╚══════════════════════════════════════════════════════════╝

def livestatus(query):
    try:
        s = unixsock.socket(unixsock.AF_UNIX, unixsock.SOCK_STREAM)
        s.connect(LIVESTATUS_SOCKET)
        s.send((query.strip() + "\n\n").encode())
        s.shutdown(unixsock.SHUT_WR)
        chunks = []
        while True:
            data = s.recv(65536)
            if not data: break
            chunks.append(data)
        s.close()
        raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
        return json.loads(raw) if raw else []
    except Exception as e:
        print(f"  [livestatus] ERROR: {e}"); return []

def get_hosts():
    rows = livestatus("GET hosts\nColumns: name address state plugin_output\nOutputFormat: json\n")
    return [{"name":r[0],"address":r[1],"state":str(r[2]),"plugin_output":r[3]}
            for r in rows if r[0] not in SKIP_HOSTS]

def get_services():
    rows = livestatus("GET services\nColumns: host_name description state plugin_output perf_data\nOutputFormat: json\n")
    return [{"host_name":r[0],"description":r[1],"state":str(r[2]),
             "plugin_output":r[3],"perf_data":r[4]} for r in rows]

def parse_pct(text):
    m = re.search(r"([\d.]+)\s*%", text or "")
    return float(m.group(1)) if m else None

def parse_vsphere_mem(text):
    m = re.search(r"Usage:\s*([\d.]+)%", text or "")
    return float(m.group(1)) if m else parse_pct(text)

def parse_fs_pct(text):
    m = re.search(r"Used:\s*([\d.]+)%", text or "")
    if m: return float(m.group(1))
    m2 = re.search(r"([\d.]+)\s*(TiB|GiB|MiB).*?of.*?([\d.]+)\s*(TiB|GiB|MiB)",
                   text or "", re.IGNORECASE)
    if m2:
        def to_g(v,u):
            u=u.upper()
            return float(v)*1024 if u=="TIB" else float(v)/1024 if u=="MIB" else float(v)
        used,total=to_g(m2.group(1),m2.group(2)),to_g(m2.group(3),m2.group(4))
        return round(used/total*100,1) if total>0 else None
    return None

def fmt_pct(v): return f"{v:.1f} %" if v is not None else "N/A"

def parse_uptime(text):
    m = re.search(r"uptime:\s*(\d+)\s*days?,\s*(\d+):(\d+)", text or "")
    if m: return f"{m.group(1)}d {m.group(2)}h"
    d=re.search(r"(\d+)\s*day",text or "")
    h=re.search(r"(\d+)\s*hour",text or "")
    mn=re.search(r"(\d+)\s*min",text or "")
    parts=[]
    if d: parts.append(f"{d.group(1)}d")
    if h: parts.append(f"{h.group(1)}h")
    if mn: parts.append(f"{mn.group(1)}m")
    return " ".join(parts) if parts else "N/A"

def svc_match(services, hn, keywords):
    hn=hn.lower()
    for kw in keywords:
        for s in services:
            if s["host_name"].lower()==hn and kw.lower() in s["description"].lower():
                return s
    return {}

def get_datastores(services, hn):
    hn=hn.lower(); stores=[]
    for s in services:
        if s["host_name"].lower()!=hn: continue
        desc=s["description"]
        if not desc.lower().startswith("filesystem"): continue
        out=s["plugin_output"]
        pct=parse_fs_pct(out)
        name=desc[len("Filesystem "):].strip() if desc.lower().startswith("filesystem ") else desc
        stores.append((name, None if "inaccessible" in out.lower() else pct, out))
    return stores

def state_info(raw):
    return {"0":("UP","#00873d","#e6f9ee"),"1":("DOWN","#c62828","#fdecea"),
            "2":("UNREACHABLE","#e65100","#fff3e0")}.get(str(raw),("UNKNOWN","#777","#eee"))

def bar_color(v):
    if v is None: return "#cccccc"
    if v>=90: return "#c62828"
    if v>=75: return "#e65100"
    if v>=50: return "#f9a825"
    return "#00873d"

def rl_color(v):
    if v is None: return colors.grey
    if v>=90: return colors.HexColor("#c62828")
    if v>=75: return colors.HexColor("#e65100")
    if v>=50: return colors.HexColor("#f9a825")
    return colors.HexColor("#00873d")

def enrich(hosts, services):
    out=[]
    for h in hosts:
        hn=h["name"]; is_vs=hn in VSPHERE_HOSTS
        if is_vs:
            cpu_s=svc_match(services,hn,["CPU utilization"])
            mem_s=svc_match(services,hn,["Memory"])
            upt_s=svc_match(services,hn,["Uptime"])
            cpu_v=parse_pct(cpu_s.get("plugin_output","")) if cpu_s else None
            mem_v=parse_vsphere_mem(mem_s.get("plugin_output","")) if mem_s else None
        else:
            cpu_s=svc_match(services,hn,["CPU utilization","CPU load","Processor"])
            mem_s=svc_match(services,hn,["Memory","RAM"])
            upt_s=svc_match(services,hn,["Uptime"])
            cpu_v=parse_pct(cpu_s.get("plugin_output","")) if cpu_s else None
            mem_v=parse_pct(mem_s.get("plugin_output","")) if mem_s else None
        upt_v=parse_uptime(upt_s.get("plugin_output","")) if upt_s else "N/A"
        stores=get_datastores(services,hn)
        disk_v=max((p for _,p,_ in stores if p is not None),default=None)
        out.append({"hostname":hn,"ip":h["address"] or "N/A","state_raw":h["state"],
                    "output":h["plugin_output"][:65],"cpu":cpu_v,"memory":mem_v,
                    "disk":disk_v,"uptime":upt_v,"cpu_str":fmt_pct(cpu_v),
                    "mem_str":fmt_pct(mem_v),"disk_str":fmt_pct(disk_v),"datastores":stores})
    return out


# ╔══════════════════════════════════════════════════════════╗
# ║  SECTION 2 — DELL EMC POWERVAULT ME5024                 ║
# ╚══════════════════════════════════════════════════════════╝

EMC_SESSION = None

def emc_login():
    global EMC_SESSION
    h = hashlib.sha256(f"{EMC_USERNAME}_{EMC_PASSWORD}".encode()).hexdigest()
    r = requests.get(f"{EMC_BASE_URL}/login/{h}",
                     headers={"dataType":"json"}, verify=False, timeout=15)
    EMC_SESSION = r.json()["status"][0]["response"]

def emc_get(path):
    url = path if path.startswith("https://") else f"{EMC_BASE_URL}/{path}"
    r = requests.get(url, headers={"dataType":"json","sessionKey":EMC_SESSION},
                     verify=False, timeout=15)
    return r.json()

def emc_list(endpoint):
    data=emc_get(endpoint)
    for k,v in data.items():
        if isinstance(v,list) and k!="status": return v
    return []

def emc_item(summary):
    url=summary.get("url")
    if not url: return summary
    data=emc_get(url)
    for k,v in data.items():
        if isinstance(v,list) and k!="status" and v: return v[0]
    return summary

def emc_health(h):
    h=str(h).lower()
    if h in ["ok","good","informational","operational","up","online"]: return 0
    if h in ["degraded","warning","marginal"]: return 1
    return 2

def get_emc_data():
    """Fetch all PowerVault metrics. Returns dict or None on failure."""
    try:
        emc_login()
    except Exception as e:
        print(f"  [EMC] Login failed: {e}"); return None

    def safe(fn):
        try: return fn()
        except Exception as e: print(f"  [EMC] {e}"); return None

    def fetch_system():
        data=emc_get("system"); si=data.get("system",[{}])[0]
        return {"health":si.get("health","?"),"model":si.get("product-id","ME5024"),
                "name":si.get("system-name","Storage Array"),
                "fw":si.get("bundle-version",si.get("sc-fw","?")),
                "reason":si.get("health-reason","")}

    def fetch_controllers():
        items=[]
        for s in emc_list("controllers"):
            i=emc_item(s)
            items.append({"id":i.get("controller-id","?"),"health":i.get("health","?"),
                          "status":i.get("status","?"),"ip":i.get("ip-address","?"),
                          "fw":i.get("sc-fw",i.get("bundle-version","?"))})
        return items

    def fetch_disks():
        items=[]
        for s in emc_list("drives"):
            i=emc_item(s)
            items.append({"loc":i.get("location","?"),"health":i.get("health","?"),
                          "status":i.get("status","?"),"vendor":i.get("vendor","?"),
                          "model":i.get("model","?"),
                          "size":i.get("size",i.get("formatted-size","?"))})
        return items

    def fetch_disk_groups():
        items=[]
        for s in emc_list("disk-groups"):
            i=emc_item(s)
            items.append({"name":i.get("name","?"),"health":i.get("health","?"),
                          "status":i.get("status","?"),
                          "raid":i.get("raidtype",i.get("raid","?")),
                          "size":i.get("size",i.get("total-size","?")),
                          "free":i.get("freespace",i.get("free-size","?"))})
        return items

    def fetch_volumes():
        items=[]
        for s in emc_list("volumes"):
            i=emc_item(s)
            items.append({"name":i.get("volume-name",i.get("name","?")),
                          "health":i.get("health","?"),
                          "size":i.get("total-size",i.get("size","?")),
                          "owner":i.get("owner","?")})
        return items

    def fetch_fans():
        items=[]
        for s in emc_list("fans"):
            i=emc_item(s)
            items.append({"name":i.get("name",i.get("location",i.get("durable-id","?"))),
                          "health":i.get("health","?"),"status":i.get("status","?")})
        return items

    def fetch_psus():
        items=[]
        for s in emc_list("power-supplies"):
            i=emc_item(s)
            items.append({"name":i.get("name",i.get("location",i.get("durable-id","?"))),
                          "health":i.get("health","?"),"status":i.get("status","?")})
        return items

    def fetch_ports():
        items=[]
        for s in emc_list("ports"):
            i=emc_item(s)
            items.append({"name":i.get("port",i.get("name","?")),
                          "health":i.get("health","?"),"status":i.get("status","?"),
                          "type":i.get("port-type",i.get("type","?"))})
        return items

    return {
        "system":      safe(fetch_system),
        "controllers": safe(fetch_controllers) or [],
        "disks":       safe(fetch_disks) or [],
        "disk_groups": safe(fetch_disk_groups) or [],
        "volumes":     safe(fetch_volumes) or [],
        "fans":        safe(fetch_fans) or [],
        "psus":        safe(fetch_psus) or [],
        "ports":       safe(fetch_ports) or [],
    }


# ╔══════════════════════════════════════════════════════════╗
# ║  SECTION 3 — GRAPHS                                     ║
# ╚══════════════════════════════════════════════════════════╝

def shorten(name, n=20):
    subs=[("Filesystem ",""),("Datastore","DS"),("NHLVMware ",""),
          ("NHL ",""),("HYPERV-","HV-"),("SERVER","SRV"),("-PROD","")]
    for o,nw in subs: name=name.replace(o,nw)
    return name[:n-2]+".." if len(name)>n else name

def trunc_hn(name, n=14):
    """Truncate hostname for display in tight spaces."""
    return name if len(name)<=n else name[:n-2]+".."

def make_host_graph(host):
    if not MPL_OK: return None
    metrics=[("CPU",host["cpu"]),("Memory",host["memory"]),("Disk",host["disk"])]
    if all(v is None for _,v in metrics): return None
    labels=[m[0] for m in metrics]
    values=[m[1] if m[1] is not None else 0 for m in metrics]
    bcolors=[bar_color(m[1]) for m in metrics]
    fig,ax=plt.subplots(figsize=(5.5,1.25))
    fig.patch.set_facecolor("#f8faff"); ax.set_facecolor("#f8faff")
    bars=ax.barh(labels,values,color=bcolors,height=0.52,zorder=3)
    ax.set_xlim(0,112)
    ax.axvline(75,color="#e65100",linestyle="--",linewidth=0.7,alpha=0.5)
    ax.axvline(90,color="#c62828",linestyle="--",linewidth=0.7,alpha=0.5)
    ax.set_xlabel("%",fontsize=7,color="#777",labelpad=2)
    ax.tick_params(axis="y",labelsize=9,colors="#333",pad=3)
    ax.tick_params(axis="x",labelsize=7,colors="#888")
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.grid(axis="x",linestyle="--",alpha=0.3,zorder=0)
    for bar,(_,v) in zip(bars,metrics):
        if v is not None:
            ax.text(min(v+0.8,103),bar.get_y()+bar.get_height()/2,
                    f"{v:.1f}%",va="center",fontsize=8,color="#222",fontweight="bold")
        else:
            ax.text(1.5,bar.get_y()+bar.get_height()/2,"N/A",va="center",fontsize=8,color="#aaa")
    plt.tight_layout(pad=0.3)
    os.makedirs(GRAPH_DIR,exist_ok=True)
    safe=host["hostname"].replace("/","_").replace(" ","_")
    path=os.path.join(GRAPH_DIR,f"host_{safe}.png")
    plt.savefig(path,dpi=150,bbox_inches="tight",facecolor=fig.get_facecolor())
    plt.close(); return path

def make_datastore_graph(host):
    if not MPL_OK: return None
    stores=[(shorten(n,22),p) for n,p,_ in host["datastores"] if p is not None]
    if not stores: return None
    stores.sort(key=lambda x:x[1],reverse=True)
    names=[s[0] for s in stores]; values=[s[1] for s in stores]
    bcolors=[bar_color(v) for v in values]
    fig_h=max(2.0,len(names)*0.42+0.9)
    fig,ax=plt.subplots(figsize=(5.5,fig_h))
    fig.patch.set_facecolor("#f8faff"); ax.set_facecolor("#f8faff")
    bars=ax.barh(names,values,color=bcolors,height=0.55,zorder=3)
    ax.set_xlim(0,112)
    ax.axvline(75,color="#e65100",linestyle="--",linewidth=0.7,alpha=0.5)
    ax.axvline(90,color="#c62828",linestyle="--",linewidth=0.7,alpha=0.5)
    ax.set_xlabel("Usage (%)",fontsize=8,color="#777")
    ax.set_title(f"{host['hostname']} — Datastores",fontsize=9,
                 fontweight="bold",color="#0d2b5e",pad=5)
    ax.tick_params(axis="y",labelsize=8,colors="#333",pad=3)
    ax.tick_params(axis="x",labelsize=7.5,colors="#888")
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.grid(axis="x",linestyle="--",alpha=0.3,zorder=0)
    for bar,val in zip(bars,values):
        ax.text(min(val+0.8,103),bar.get_y()+bar.get_height()/2,
                f"{val:.1f}%",va="center",fontsize=8,color="#222",fontweight="bold")
    plt.tight_layout(pad=0.4)
    safe=host["hostname"].replace("/","_").replace(" ","_")
    path=os.path.join(GRAPH_DIR,f"ds_{safe}.png")
    plt.savefig(path,dpi=150,bbox_inches="tight",facecolor=fig.get_facecolor())
    plt.close(); return path

def make_overview(hosts):
    os.makedirs(GRAPH_DIR,exist_ok=True); paths={}
    if not MPL_OK: return paths
    up=sum(1 for h in hosts if h["state_raw"]=="0")
    down=sum(1 for h in hosts if h["state_raw"]=="1")
    unr=sum(1 for h in hosts if h["state_raw"]=="2")
    other=len(hosts)-up-down-unr
    sizes=[x for x in [up,down,unr,other] if x>0]
    labels=[l for l,x in zip(["UP","DOWN","UNREACHABLE","UNKNOWN"],[up,down,unr,other]) if x>0]
    clrs=[c for c,x in zip(["#00873d","#c62828","#e65100","#aaa"],[up,down,unr,other]) if x>0]
    fig,ax=plt.subplots(figsize=(4,3.2)); fig.patch.set_facecolor("#f8faff")
    w,t,a=ax.pie(sizes,labels=labels,colors=clrs,autopct="%1.0f%%",startangle=90,
                 wedgeprops=dict(width=0.55,edgecolor="white",linewidth=2))
    for tx in t: tx.set_fontsize(10)
    for at in a: at.set_fontsize(9); at.set_color("white"); at.set_fontweight("bold")
    ax.set_title("Host States",fontsize=11,fontweight="bold",color="#0d2b5e",pad=6)
    plt.tight_layout()
    p=os.path.join(GRAPH_DIR,"overview_pie.png")
    plt.savefig(p,dpi=130,bbox_inches="tight",facecolor=fig.get_facecolor())
    plt.close(); paths["pie"]=p

    alerts=[(shorten(h["hostname"],15),h["cpu"] or 0,h["memory"] or 0,h["disk"] or 0)
            for h in hosts if any(v is not None and v>=75 for v in [h["cpu"],h["memory"],h["disk"]])]
    if alerts:
        alerts.sort(key=lambda x:max(x[1],x[2],x[3]),reverse=True)
        top=alerts[:12]; names=[a[0] for a in top]
        cpus=[a[1] for a in top]; mems=[a[2] for a in top]; disks=[a[3] for a in top]
        x=np.arange(len(names)); w=0.25
        fig_h=max(4,len(names)*0.55+1.5)
        fig,ax=plt.subplots(figsize=(10,fig_h))
        fig.patch.set_facecolor("#f8faff"); ax.set_facecolor("#f8faff")
        ax.barh(x+w,cpus,w,label="CPU",color="#4a90d9",zorder=3)
        ax.barh(x,mems,w,label="Memory",color="#e67e22",zorder=3)
        ax.barh(x-w,disks,w,label="Disk",color="#27ae60",zorder=3)
        ax.set_yticks(x); ax.set_yticklabels(names,fontsize=10)
        ax.set_xlim(0,115)
        ax.axvline(75,color="#e65100",linestyle="--",linewidth=0.8,alpha=0.6)
        ax.axvline(90,color="#c62828",linestyle="--",linewidth=0.8,alpha=0.6)
        ax.set_xlabel("Usage (%)",fontsize=10)
        ax.set_title("Hosts with High Resource Usage (≥75%)",fontsize=11,
                     fontweight="bold",color="#0d2b5e",pad=8)
        ax.legend(loc="lower right",fontsize=9,framealpha=0.8)
        ax.grid(axis="x",linestyle="--",alpha=0.3,zorder=0)
        ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
        plt.tight_layout()
        p2=os.path.join(GRAPH_DIR,"alerts_chart.png")
        plt.savefig(p2,dpi=130,bbox_inches="tight",facecolor=fig.get_facecolor())
        plt.close(); paths["alerts"]=p2
    return paths

def make_emc_disk_graph(emc):
    """Health status bar chart for EMC disks."""
    if not MPL_OK or not emc.get("disks"): return None
    disks=emc["disks"]
    ok=sum(1 for d in disks if emc_health(d["health"])==0)
    warn=sum(1 for d in disks if emc_health(d["health"])==1)
    crit=sum(1 for d in disks if emc_health(d["health"])==2)
    fig,ax=plt.subplots(figsize=(4,2.2)); fig.patch.set_facecolor("#f8faff"); ax.set_facecolor("#f8faff")
    bars=ax.barh(["Critical","Warning","Healthy"],[crit,warn,ok],
                 color=["#c62828","#f9a825","#00873d"],height=0.5,zorder=3)
    ax.set_xlim(0,max(ok,warn,crit,1)*1.25)
    ax.set_title(f"Disk Health ({len(disks)} total)",fontsize=9,fontweight="bold",color="#0d2b5e")
    ax.tick_params(labelsize=8); ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.grid(axis="x",linestyle="--",alpha=0.3,zorder=0)
    for bar,val in zip(bars,[crit,warn,ok]):
        if val>0:
            ax.text(val+0.1,bar.get_y()+bar.get_height()/2,str(val),
                    va="center",fontsize=9,fontweight="bold")
    plt.tight_layout(pad=0.4)
    path=os.path.join(GRAPH_DIR,"emc_disks.png")
    plt.savefig(path,dpi=130,bbox_inches="tight",facecolor=fig.get_facecolor())
    plt.close(); return path

def img_b64(path):
    with open(path,"rb") as f: return base64.b64encode(f.read()).decode()


# ╔══════════════════════════════════════════════════════════╗
# ║  SECTION 4 — PDF BUILDER                               ║
# ╚══════════════════════════════════════════════════════════╝

def build_pdf(hosts, overview_paths, emc):
    if not REPORTLAB_OK:
        print("  Skipping PDF."); return False

    doc=SimpleDocTemplate(PDF_PATH,pagesize=A4,
                          leftMargin=1.8*cm,rightMargin=1.8*cm,
                          topMargin=1.5*cm,bottomMargin=1.5*cm)
    styles=getSampleStyleSheet()
    now=datetime.datetime.now().strftime("%Y-%m-%d %H:%M EAT")
    pw=A4[0]-3.6*cm

    T =ParagraphStyle("T",parent=styles["Title"],fontSize=17,textColor=colors.HexColor("#0d2b5e"),spaceAfter=3)
    S =ParagraphStyle("S",parent=styles["Normal"],fontSize=9,textColor=colors.grey,spaceAfter=5)
    H =ParagraphStyle("H",parent=styles["Heading2"],fontSize=12,textColor=colors.HexColor("#0d2b5e"),spaceBefore=10,spaceAfter=5)
    H3=ParagraphStyle("H3",parent=styles["Heading3"],fontSize=9,textColor=colors.HexColor("#1e3a5f"),spaceBefore=3,spaceAfter=2)
    FT=ParagraphStyle("FT",parent=styles["Normal"],fontSize=8,textColor=colors.grey,alignment=1)
    DARK=colors.HexColor("#0d2b5e"); STRIPE=colors.HexColor("#f4f8ff")

    elems=[]

    # ── Title ──
    elems+=[Paragraph("Nakasero Hospital — Daily Infrastructure Report",T),
            Paragraph(f"Generated: {now}",S),
            HRFlowable(width="100%",thickness=1,color=colors.HexColor("#dde5f0")),Spacer(1,8)]

    # ── Summary cards ──
    up=sum(1 for h in hosts if h["state_raw"]=="0")
    down=sum(1 for h in hosts if h["state_raw"]=="1")
    unr=len(hosts)-up-down
    smry=[["Total Hosts","UP","DOWN","UNREACHABLE"],[str(len(hosts)),str(up),str(down),str(unr)]]
    st=Table(smry,colWidths=[pw*0.28,pw*0.24,pw*0.24,pw*0.24])
    st.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),DARK),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,0),9),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),("FONTNAME",(0,1),(-1,-1),"Helvetica-Bold"),
        ("FONTSIZE",(0,1),(-1,-1),18),
        ("TEXTCOLOR",(1,1),(1,1),colors.HexColor("#00873d")),
        ("TEXTCOLOR",(2,1),(2,1),colors.HexColor("#c62828") if down>0 else colors.HexColor("#00873d")),
        ("TEXTCOLOR",(3,1),(3,1),colors.HexColor("#e65100") if unr>0 else colors.HexColor("#00873d")),
        ("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#dde5f0")),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.whitesmoke]),
        ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
    ]))
    elems+=[st,Spacer(1,8)]

    # ── Overview charts ──
    pie_p=overview_paths.get("pie"); alerts_p=overview_paths.get("alerts")
    if pie_p and os.path.exists(pie_p) and alerts_p and os.path.exists(alerts_p):
        ct=Table([[Image(pie_p,width=pw*0.36,height=pw*0.30),
                   Image(alerts_p,width=pw*0.62,height=pw*0.30)]],colWidths=[pw*0.38,pw*0.62])
        ct.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
        elems+=[ct,Spacer(1,6)]
    elif pie_p and os.path.exists(pie_p):
        elems+=[Image(pie_p,width=pw*0.38,height=pw*0.30),Spacer(1,6)]

    elems+=[HRFlowable(width="100%",thickness=1,color=colors.HexColor("#dde5f0")),Spacer(1,5)]

    # ── Host Summary Table ──
    elems.append(Paragraph("Host Summary",H))
    hdr_row=["Hostname","IP Address","State","CPU","Memory","Disk (max)","Uptime","Status Output"]
    cw=[pw*0.16,pw*0.12,pw*0.08,pw*0.07,pw*0.07,pw*0.08,pw*0.08,pw*0.34]
    tdata=[hdr_row]
    for h in hosts:
        lbl,_,_=state_info(h["state_raw"])
        tdata.append([h["hostname"],h["ip"],lbl,h["cpu_str"],h["mem_str"],
                      h["disk_str"],h["uptime"],h["output"][:50]])
    sumtbl=Table(tdata,colWidths=cw,repeatRows=1)
    ts=[
        ("BACKGROUND",(0,0),(-1,0),DARK),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7.5),
        ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#dde5f0")),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,STRIPE]),
        ("ALIGN",(2,0),(5,-1),"CENTER"),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]
    for i,h in enumerate(hosts,start=1):
        lbl,_,_=state_info(h["state_raw"])
        ts.append(("TEXTCOLOR",(2,i),(2,i),
                   {"UP":colors.HexColor("#00873d"),"DOWN":colors.HexColor("#c62828"),
                    "UNREACHABLE":colors.HexColor("#e65100")}.get(lbl,colors.grey)))
        ts.append(("FONTNAME",(2,i),(2,i),"Helvetica-Bold"))
        for ci,key in [(3,"cpu"),(4,"memory"),(5,"disk")]:
            ts.append(("TEXTCOLOR",(ci,i),(ci,i),rl_color(h[key])))
            ts.append(("FONTNAME",(ci,i),(ci,i),"Helvetica-Bold"))
    sumtbl.setStyle(TableStyle(ts))
    elems+=[sumtbl,Spacer(1,6)]

    # ════ PAGE 2: Per-Host Detail ════
    elems.append(PageBreak())
    elems.append(Paragraph("Per-Host Detail with Graphs",H))
    elems.append(Spacer(1,6))

    hn_style=ParagraphStyle("HN",parent=styles["Normal"],fontSize=8.5,
                             textColor=colors.white,fontName="Helvetica-Bold",leading=11)
    sub_style=ParagraphStyle("SB",parent=styles["Normal"],fontSize=7.5,
                              textColor=colors.HexColor("#a8c6e8"),leading=10)

    for h in hosts:
        lbl,_,_=state_info(h["state_raw"])
        g_path=make_host_graph(h)
        ds_path=make_datastore_graph(h) if h["datastores"] else None
        state_clr={"UP":colors.HexColor("#00873d"),"DOWN":colors.HexColor("#c62828"),
                   "UNREACHABLE":colors.HexColor("#e65100")}.get(lbl,colors.grey)

        # Fixed-width header: hostname truncated so it never wraps
        hn_disp=trunc_hn(h["hostname"],13)
        hdr_data=[[
            Paragraph(hn_disp,hn_style),
            Paragraph(h["ip"],sub_style),
            Paragraph(lbl,ParagraphStyle("ST",parent=styles["Normal"],fontSize=9,
                                          textColor=state_clr,fontName="Helvetica-Bold")),
            Paragraph(f"CPU: {h['cpu_str']}",ParagraphStyle("M1",parent=styles["Normal"],
                      fontSize=8,textColor=rl_color(h["cpu"]))),
            Paragraph(f"Mem: {h['mem_str']}",ParagraphStyle("M2",parent=styles["Normal"],
                      fontSize=8,textColor=rl_color(h["memory"]))),
            Paragraph(f"Disk: {h['disk_str']}",ParagraphStyle("M3",parent=styles["Normal"],
                      fontSize=8,textColor=rl_color(h["disk"]))),
            Paragraph(f"Up: {h['uptime']}",ParagraphStyle("M4",parent=styles["Normal"],
                      fontSize=8,textColor=colors.HexColor("#aaaaaa"))),
        ]]
        hdr_cw=[pw*0.15,pw*0.13,pw*0.09,pw*0.15,pw*0.15,pw*0.16,pw*0.17]
        hdr_tbl=Table(hdr_data,colWidths=hdr_cw)
        hdr_tbl.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#1e3a5f")),
            ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
            ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),4),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ]))

        block=[hdr_tbl]
        if g_path and os.path.exists(g_path):
            block.append(Image(g_path,width=pw,height=pw*0.13))

        if ds_path and os.path.exists(ds_path):
            n_s=len([x for x in h["datastores"] if x[1] is not None])
            dh=max(pw*0.16,n_s*pw*0.052)
            block+=[Spacer(1,2),Paragraph("Datastore Usage:",H3),
                    Image(ds_path,width=pw,height=min(dh,pw*0.52))]

        if h["datastores"]:
            ds_rows=[["Datastore","Used %","Details"]]
            for name,pct,raw in sorted(h["datastores"],key=lambda x:(x[1] or -1),reverse=True):
                detail=""
                m=re.search(r"([\d.]+\s*(?:TiB|GiB|MiB))\s*of\s*([\d.]+\s*(?:TiB|GiB|MiB))",raw or "")
                if m: detail=f"{m.group(1)} of {m.group(2)}"
                elif "inaccessible" in (raw or "").lower(): detail="⚠ inaccessible"
                ds_rows.append([name[:38],fmt_pct(pct),detail])
            dstbl=Table(ds_rows,colWidths=[pw*0.44,pw*0.14,pw*0.42],repeatRows=1)
            ds_ts=[
                ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#2c4a7c")),
                ("TEXTCOLOR",(0,0),(-1,0),colors.white),
                ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                ("FONTSIZE",(0,0),(-1,-1),7),
                ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#dde5f0")),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f4f8ff")]),
                ("ALIGN",(1,0),(1,-1),"CENTER"),
                ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
            ]
            for i,(_,pct,_) in enumerate(sorted(h["datastores"],key=lambda x:(x[1] or -1),reverse=True),start=1):
                ds_ts.append(("TEXTCOLOR",(1,i),(1,i),rl_color(pct)))
                ds_ts.append(("FONTNAME",(1,i),(1,i),"Helvetica-Bold"))
            dstbl.setStyle(TableStyle(ds_ts))
            block+=[Spacer(1,2),dstbl]

        block.append(Spacer(1,10))
        elems.append(KeepTogether(block))

    # ════ PAGE 3: Dell EMC PowerVault ME5024 ════
    if emc:
        elems.append(PageBreak())
        elems.append(Paragraph("🗄️  Dell EMC PowerVault ME5024 — Storage Report",H))
        elems.append(Spacer(1,6))

        EMC_HDR=colors.HexColor("#1a4a6b")
        EMC_STRIPE=colors.HexColor("#eef4fa")

        def emc_tbl(data,cws,header_bg=EMC_HDR):
            t=Table(data,colWidths=cws,repeatRows=1)
            s=[
                ("BACKGROUND",(0,0),(-1,0),header_bg),
                ("TEXTCOLOR",(0,0),(-1,0),colors.white),
                ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                ("FONTSIZE",(0,0),(-1,-1),7.5),
                ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#ccd9e8")),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,EMC_STRIPE]),
                ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ]
            t.setStyle(TableStyle(s)); return t

        def health_badge(h_str):
            st=emc_health(h_str)
            c=colors.HexColor("#00873d") if st==0 else colors.HexColor("#c62828") if st==2 else colors.HexColor("#e65100")
            return Paragraph(str(h_str),ParagraphStyle("HB",parent=styles["Normal"],
                             fontSize=7.5,textColor=c,fontName="Helvetica-Bold"))

        # System Health
        sys=emc.get("system",{})
        if sys:
            sys_state=emc_health(sys.get("health","?"))
            sys_clr=colors.HexColor("#e6f9ee") if sys_state==0 else colors.HexColor("#fdecea")
            sys_tbl=Table([[
                Paragraph(f"System: {sys.get('name','?')}",
                          ParagraphStyle("SN",parent=styles["Normal"],fontSize=10,
                                         fontName="Helvetica-Bold",textColor=colors.HexColor("#0d2b5e"))),
                Paragraph(f"Model: {sys.get('model','?')}",styles["Normal"]),
                Paragraph(f"FW: {sys.get('fw','?')}",styles["Normal"]),
                health_badge(sys.get("health","?")),
            ]],colWidths=[pw*0.35,pw*0.25,pw*0.25,pw*0.15])
            sys_tbl.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),sys_clr),
                ("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#ccd9e8")),
                ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
                ("LEFTPADDING",(0,0),(-1,-1),8),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ]))
            elems+=[sys_tbl,Spacer(1,10)]

        # Controllers + Disk stats side by side
        ctrl_data=[["Controller","Status","Health","IP Address","Firmware"]]
        for c in emc.get("controllers",[]):
            ctrl_data.append([c["id"],c["status"],health_badge(c["health"]),c["ip"],c["fw"]])

        disks=emc.get("disks",[])
        ok_d=sum(1 for d in disks if emc_health(d["health"])==0)
        warn_d=sum(1 for d in disks if emc_health(d["health"])==1)
        crit_d=sum(1 for d in disks if emc_health(d["health"])==2)

        disk_graph_path=make_emc_disk_graph(emc)
        if ctrl_data and disk_graph_path and os.path.exists(disk_graph_path):
            ctrl_tbl=emc_tbl(ctrl_data,[pw*0.10,pw*0.20,pw*0.15,pw*0.30,pw*0.25])
            side=Table([[ctrl_tbl,Image(disk_graph_path,width=pw*0.38,height=pw*0.28)]],
                       colWidths=[pw*0.60,pw*0.40])
            side.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
                                      ("LEFTPADDING",(1,0),(1,0),8)]))
            elems+=[Paragraph("Controllers & Disk Health",H3),side,Spacer(1,8)]

        # Disk Groups
        if emc.get("disk_groups"):
            dg_data=[["Disk Group","RAID","Status","Health","Size","Free Space"]]
            for dg in emc["disk_groups"]:
                dg_data.append([dg["name"],dg["raid"],dg["status"],
                                 health_badge(dg["health"]),dg["size"],dg["free"]])
            elems+=[Paragraph("Disk Groups",H3),
                    emc_tbl(dg_data,[pw*0.20,pw*0.12,pw*0.18,pw*0.15,pw*0.18,pw*0.17]),
                    Spacer(1,8)]

        # Volumes
        if emc.get("volumes"):
            vol_data=[["Volume Name","Health","Size","Owner"]]
            for v in emc["volumes"]:
                vol_data.append([v["name"][:40],health_badge(v["health"]),
                                  v["size"],f"Controller {v['owner']}"])
            elems+=[Paragraph("Volumes",H3),
                    emc_tbl(vol_data,[pw*0.45,pw*0.15,pw*0.20,pw*0.20]),
                    Spacer(1,8)]

        # Fans + PSUs side by side
        fan_data=[["Fan","Status","Health"]]
        for f in emc.get("fans",[]): fan_data.append([f["name"],f["status"],health_badge(f["health"])])
        psu_data=[["PSU","Status","Health"]]
        for p in emc.get("psus",[]): psu_data.append([p["name"],p["status"],health_badge(p["health"])])
        if len(fan_data)>1 or len(psu_data)>1:
            ft=emc_tbl(fan_data,[pw*0.25*0.48,pw*0.25*0.32,pw*0.25*0.20]) if len(fan_data)>1 else Spacer(1,1)
            pt=emc_tbl(psu_data,[pw*0.25*0.48,pw*0.25*0.32,pw*0.25*0.20]) if len(psu_data)>1 else Spacer(1,1)
            hw=Table([[Paragraph("Fans",H3),Paragraph("Power Supplies",H3)],
                       [ft,pt]],colWidths=[pw*0.48,pw*0.52])
            hw.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
                                    ("LEFTPADDING",(1,0),(1,-1),10)]))
            elems+=[hw,Spacer(1,8)]

        # Ports
        if emc.get("ports"):
            port_data=[["Port","Type","Status","Health"]]
            for p in emc["ports"]:
                port_data.append([p["name"],p["type"],p["status"],health_badge(p["health"])])
            elems+=[Paragraph("Host Ports",H3),
                    emc_tbl(port_data,[pw*0.15,pw*0.20,pw*0.30,pw*0.35]),
                    Spacer(1,8)]

    elems+=[HRFlowable(width="100%",thickness=0.5,color=colors.HexColor("#dde5f0")),
            Spacer(1,4),
            Paragraph(f"Auto-generated · Nakasero Hospital IT Dept · {now}",FT)]
    doc.build(elems)
    print(f"  PDF saved: {PDF_PATH}")
    return True


# ╔══════════════════════════════════════════════════════════╗
# ║  SECTION 5 — HTML BUILDER                              ║
# ╚══════════════════════════════════════════════════════════╝

def build_html(hosts, overview_paths, emc):
    now=datetime.datetime.now().strftime("%A, %d %B %Y — %H:%M EAT")
    today=datetime.datetime.now().strftime("%Y-%m-%d")
    up=sum(1 for h in hosts if h["state_raw"]=="0")
    down=sum(1 for h in hosts if h["state_raw"]=="1")
    unr=sum(1 for h in hosts if h["state_raw"]=="2")
    total=len(hosts)

    def card(v,l,bg,fg):
        return (f'<td style="width:25%;padding:5px;">'
                f'<div style="background:{bg};border-radius:10px;padding:18px 8px;text-align:center;">'
                f'<div style="font-size:36px;font-weight:900;color:{fg};">{v}</div>'
                f'<div style="font-size:10px;font-weight:700;color:{fg};opacity:.85;'
                f'margin-top:5px;letter-spacing:.8px;">{l}</div></div></td>')
    cards=(card(total,"TOTAL","#1e3a5f","#fff")+card(up,"UP","#e6f9ee","#00873d")+
           card(down,"DOWN","#fdecea","#c62828")+card(unr,"UNREACHABLE","#fff3e0","#e65100"))

    def gimg(path,alt,w="100%"):
        if path and os.path.exists(path):
            return (f'<img src="data:image/png;base64,{img_b64(path)}" alt="{alt}" '
                    f'style="width:{w};border-radius:6px;border:1px solid #dde5f0;">')
        return ""

    def mspan(val,sval):
        if val is None: return f'<span style="color:#bbb;">{sval}</span>'
        c="#c62828" if val>=90 else "#e65100" if val>=75 else "#f9a825" if val>=50 else "#00873d"
        return f'<span style="color:{c};font-weight:700;">{sval}</span>'

    def eh(h_str):
        st=emc_health(h_str)
        c="#c62828" if st==2 else "#e65100" if st==1 else "#00873d"
        return f'<span style="color:{c};font-weight:700;">{h_str}</span>'

    # Summary table
    sum_rows=""
    for i,h in enumerate(hosts):
        lbl,fg,bg=state_info(h["state_raw"])
        badge=(f'<span style="background:{bg};color:{fg};border:1px solid {fg};'
               f'border-radius:3px;padding:1px 6px;font-size:10px;font-weight:700;">{lbl}</span>')
        rbg="#fff" if i%2==0 else "#f4f8ff"
        sum_rows+=(f'<tr style="background:{rbg};">'
                   f'<td style="padding:7px 12px;font-weight:700;color:#1e3a5f;font-size:12px;">{h["hostname"]}</td>'
                   f'<td style="padding:7px 12px;color:#555;font-size:11px;font-family:monospace;">{h["ip"]}</td>'
                   f'<td style="padding:7px 12px;text-align:center;">{badge}</td>'
                   f'<td style="padding:7px 12px;text-align:center;font-size:12px;">{mspan(h["cpu"],h["cpu_str"])}</td>'
                   f'<td style="padding:7px 12px;text-align:center;font-size:12px;">{mspan(h["memory"],h["mem_str"])}</td>'
                   f'<td style="padding:7px 12px;text-align:center;font-size:12px;">{mspan(h["disk"],h["disk_str"])}</td>'
                   f'<td style="padding:7px 12px;text-align:center;font-size:12px;color:#555;">{h["uptime"]}</td>'
                   f'</tr>')

    # Per-host cards
    host_cards=""
    for h in hosts:
        lbl,fg,bg=state_info(h["state_raw"])
        badge=(f'<span style="background:{bg};color:{fg};border:1px solid {fg};'
               f'border-radius:4px;padding:2px 7px;font-size:11px;font-weight:700;">{lbl}</span>')
        safe=h["hostname"].replace("/","_").replace(" ","_")
        g_path=os.path.join(GRAPH_DIR,f"host_{safe}.png")
        ds_path=os.path.join(GRAPH_DIR,f"ds_{safe}.png")

        ds_html=""
        if h["datastores"]:
            ds_rows_html=""
            for i,(name,pct,raw) in enumerate(sorted(h["datastores"],key=lambda x:(x[1] or -1),reverse=True)):
                detail=""
                m=re.search(r"([\d.]+\s*(?:TiB|GiB|MiB))\s*of\s*([\d.]+\s*(?:TiB|GiB|MiB))",raw or "")
                if m: detail=f"{m.group(1)} of {m.group(2)}"
                elif "inaccessible" in (raw or "").lower(): detail="⚠ inaccessible"; pct=None
                c=("#c62828" if pct and pct>=90 else "#e65100" if pct and pct>=75
                   else "#f9a825" if pct and pct>=50 else "#00873d" if pct else "#aaa")
                rbg="#fff" if i%2==0 else "#f4f8ff"
                ds_rows_html+=(f'<tr style="background:{rbg};">'
                               f'<td style="padding:4px 10px;font-size:11px;">{name}</td>'
                               f'<td style="padding:4px 10px;font-size:11px;text-align:center;font-weight:700;color:{c};">{fmt_pct(pct)}</td>'
                               f'<td style="padding:4px 10px;font-size:11px;color:#666;">{detail}</td></tr>')
            ds_html=f"""
            <div style="margin-top:10px;">
              <div style="font-size:10px;font-weight:700;color:#2c4a7c;text-transform:uppercase;letter-spacing:.6px;margin-bottom:5px;">Datastore Usage</div>
              {gimg(ds_path,"Datastores") if os.path.exists(ds_path) else ""}
              <table width="100%" cellspacing="0" cellpadding="0"
                     style="border-collapse:collapse;border:1px solid #dde5f0;border-radius:6px;overflow:hidden;margin-top:6px;">
                <thead><tr style="background:#2c4a7c;color:#fff;">
                  <th style="padding:5px 10px;font-size:10px;text-align:left;">Datastore</th>
                  <th style="padding:5px 10px;font-size:10px;text-align:center;">Used</th>
                  <th style="padding:5px 10px;font-size:10px;text-align:left;">Size</th>
                </tr></thead>
                <tbody>{ds_rows_html}</tbody></table></div>"""

        host_cards+=f"""
        <div style="border:1px solid #dde5f0;border-radius:10px;margin-bottom:12px;
                    overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.06);">
          <div style="background:#1e3a5f;padding:9px 16px;display:flex;
                      justify-content:space-between;align-items:center;flex-wrap:wrap;gap:5px;">
            <div style="color:#fff;font-weight:700;font-size:13px;">{h['hostname']}</div>
            <div style="color:#a8c6e8;font-size:11px;font-family:monospace;">{h['ip']}</div>
            <div>{badge}</div>
          </div>
          <div style="padding:11px 16px;">
            <table width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:9px;">
              <tr>{''.join(f"<td style='width:25%;padding:2px 6px;font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.4px;'>{l}</td>" for l in ['CPU','Memory','Disk','Uptime'])}</tr>
              <tr>
                <td style="padding:1px 6px;font-size:15px;">{mspan(h['cpu'],h['cpu_str'])}</td>
                <td style="padding:1px 6px;font-size:15px;">{mspan(h['memory'],h['mem_str'])}</td>
                <td style="padding:1px 6px;font-size:15px;">{mspan(h['disk'],h['disk_str'])}</td>
                <td style="padding:1px 6px;font-size:13px;font-weight:600;color:#555;">{h['uptime']}</td>
              </tr>
            </table>
            {gimg(g_path,f"{h['hostname']} metrics")}
            {ds_html}
            <div style="margin-top:5px;font-size:11px;color:#999;font-style:italic;">{h['output']}</div>
          </div>
        </div>"""

    # EMC Section HTML
    emc_html=""
    if emc:
        sys=emc.get("system",{})
        sys_health=sys.get("health","?") if sys else "?"
        sys_clr="#e6f9ee" if emc_health(sys_health)==0 else "#fdecea"
        sys_fg="#00873d" if emc_health(sys_health)==0 else "#c62828"

        def emc_rows(items, keys):
            html=""
            for i,item in enumerate(items):
                rbg="#fff" if i%2==0 else "#eef4fa"
                cells="".join(f'<td style="padding:6px 10px;font-size:11px;">{item.get(k,"?")}</td>'
                              for k in keys)
                html+=f'<tr style="background:{rbg};">{cells}</tr>'
            return html

        def emc_table(headers, rows_html, widths=None):
            ths="".join(f'<th style="padding:7px 10px;text-align:left;font-size:10px;">{h}</th>'
                        for h in headers)
            return (f'<table width="100%" cellspacing="0" cellpadding="0" '
                    f'style="border-collapse:collapse;border:1px solid #ccd9e8;'
                    f'border-radius:6px;overflow:hidden;margin-bottom:14px;">'
                    f'<thead><tr style="background:#1a4a6b;color:#fff;">{ths}</tr></thead>'
                    f'<tbody>{rows_html}</tbody></table>')

        ctrl_rows="".join(
            f'<tr style="background:{"#fff" if i%2==0 else "#eef4fa"};">'
            f'<td style="padding:6px 10px;font-size:11px;font-weight:700;">{c["id"]}</td>'
            f'<td style="padding:6px 10px;font-size:11px;">{c["status"]}</td>'
            f'<td style="padding:6px 10px;font-size:11px;">{eh(c["health"])}</td>'
            f'<td style="padding:6px 10px;font-size:11px;font-family:monospace;">{c["ip"]}</td>'
            f'<td style="padding:6px 10px;font-size:11px;">{c["fw"]}</td></tr>'
            for i,c in enumerate(emc.get("controllers",[])))

        vol_rows="".join(
            f'<tr style="background:{"#fff" if i%2==0 else "#eef4fa"};">'
            f'<td style="padding:6px 10px;font-size:11px;font-weight:600;">{v["name"]}</td>'
            f'<td style="padding:6px 10px;font-size:11px;">{eh(v["health"])}</td>'
            f'<td style="padding:6px 10px;font-size:11px;">{v["size"]}</td>'
            f'<td style="padding:6px 10px;font-size:11px;">Controller {v["owner"]}</td></tr>'
            for i,v in enumerate(emc.get("volumes",[])))

        dg_rows="".join(
            f'<tr style="background:{"#fff" if i%2==0 else "#eef4fa"};">'
            f'<td style="padding:6px 10px;font-size:11px;font-weight:600;">{d["name"]}</td>'
            f'<td style="padding:6px 10px;font-size:11px;">{d["raid"]}</td>'
            f'<td style="padding:6px 10px;font-size:11px;">{eh(d["health"])}</td>'
            f'<td style="padding:6px 10px;font-size:11px;">{d["size"]}</td>'
            f'<td style="padding:6px 10px;font-size:11px;">{d["free"]}</td></tr>'
            for i,d in enumerate(emc.get("disk_groups",[])))

        disk_graph_path=os.path.join(GRAPH_DIR,"emc_disks.png")
        disk_img=gimg(disk_graph_path,"EMC Disk Health","260px") if os.path.exists(disk_graph_path) else ""

        disks=emc.get("disks",[])
        ok_d=sum(1 for d in disks if emc_health(d["health"])==0)
        crit_d=[d for d in disks if emc_health(d["health"])==2]
        warn_d=[d for d in disks if emc_health(d["health"])==1]
        disk_summary=(f'<span style="color:#00873d;font-weight:700;">{ok_d} OK</span>'
                      + (f' &nbsp; <span style="color:#c62828;font-weight:700;">{len(crit_d)} FAILED</span>' if crit_d else "")
                      + (f' &nbsp; <span style="color:#e65100;font-weight:700;">{len(warn_d)} DEGRADED</span>' if warn_d else ""))

        fan_ok=sum(1 for f in emc.get("fans",[]) if emc_health(f["health"])==0)
        psu_ok=sum(1 for p in emc.get("psus",[]) if emc_health(p["health"])==0)
        fan_total=len(emc.get("fans",[])); psu_total=len(emc.get("psus",[]))
        port_up=sum(1 for p in emc.get("ports",[]) if emc_health(p["health"])==0)
        port_total=len(emc.get("ports",[]))

        emc_html=f"""
        <div style="margin-top:28px;">
          <div style="font-size:11px;font-weight:700;color:#1a4a6b;letter-spacing:1.5px;
                      text-transform:uppercase;margin-bottom:14px;">🗄️ Dell EMC PowerVault ME5024</div>

          <!-- System Health Banner -->
          <div style="background:{sys_clr};border:1px solid {sys_fg};border-radius:8px;
                      padding:14px 20px;margin-bottom:16px;display:flex;
                      justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
            <div>
              <div style="font-size:13px;font-weight:700;color:#1a4a6b;">{sys.get("name","Storage Array")} &nbsp; ({sys.get("model","ME5024")})</div>
              <div style="font-size:11px;color:#555;margin-top:3px;">Firmware: {sys.get("fw","?")} &nbsp;|&nbsp; Location: Nakasero Hospital</div>
            </div>
            <div style="font-size:20px;font-weight:900;color:{sys_fg};">● {sys_health}</div>
          </div>

          <!-- Quick Stats -->
          <table width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:16px;">
            <tr>
              {''.join(f"<td style='width:25%;padding:5px;'><div style='background:#eef4fa;border-radius:8px;padding:12px;text-align:center;'><div style='font-size:20px;font-weight:800;color:#1a4a6b;'>{v}</div><div style='font-size:10px;color:#555;margin-top:3px;'>{l}</div></div></td>"
                       for v,l in [(f"{ok_d}/{len(disks)}",f"Disks OK"),
                                   (f"{fan_ok}/{fan_total}","Fans OK"),
                                   (f"{psu_ok}/{psu_total}","PSUs OK"),
                                   (f"{port_up}/{port_total}","Ports UP")])}
            </tr>
          </table>

          <table width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:16px;">
            <tr>
              <td width="60%" style="vertical-align:top;padding-right:12px;">
                {emc_table(["Ctrl","Status","Health","IP Address","Firmware"],ctrl_rows)}
                {emc_table(["Disk Group","RAID","Health","Size","Free"],dg_rows)}
              </td>
              <td width="40%" style="vertical-align:top;">
                <div style="font-size:10px;font-weight:700;color:#1a4a6b;margin-bottom:6px;text-transform:uppercase;">Physical Disks — {len(disks)} Total</div>
                {disk_img}
                <div style="font-size:12px;margin-top:6px;">{disk_summary}</div>
              </td>
            </tr>
          </table>

          {emc_table(["Volume Name","Health","Size","Owner"],vol_rows)}
        </div>"""

    pie_img=gimg(overview_paths.get("pie"),"State","100%")
    alerts_img=gimg(overview_paths.get("alerts"),"Alerts","100%")
    dash=f"{CHECKMK_URL}/check_mk/index.py?start_url=view.py%3Fview_name%3Dallhosts"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#eef2f7;font-family:'Segoe UI',Arial,sans-serif;">
<div style="max-width:920px;margin:24px auto;background:#fff;border-radius:14px;
            overflow:hidden;box-shadow:0 4px 28px rgba(0,0,0,.12);">
  <div style="background:linear-gradient(135deg,#0d2b5e 0%,#1565a7 100%);padding:28px 36px;text-align:center;">
    <div style="font-size:10px;color:#a8c6e8;letter-spacing:3px;text-transform:uppercase;margin-bottom:7px;">Daily Infrastructure Report</div>
    <div style="font-size:22px;font-weight:800;color:#fff;">🏥 Nakasero Hospital — IT Infrastructure Status</div>
    <div style="font-size:12px;color:#7fb3d3;margin-top:7px;">{now}</div>
  </div>
  <div style="padding:26px 30px;">
    <table width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:20px;"><tr>{cards}</tr></table>
    <table width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:20px;">
      <tr><td width="36%" style="padding-right:10px;vertical-align:top;">{pie_img}</td>
          <td width="64%" style="vertical-align:top;">{alerts_img}</td></tr></table>

    <div style="font-size:11px;font-weight:700;color:#1e3a5f;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:12px;">📋 Host Summary</div>
    <div style="overflow-x:auto;margin-bottom:26px;">
    <table width="100%" cellspacing="0" cellpadding="0"
           style="border-collapse:collapse;border:1px solid #dde5f0;border-radius:8px;overflow:hidden;min-width:700px;">
      <thead><tr style="background:#0d2b5e;color:#fff;">
        <th style="padding:10px 12px;text-align:left;font-size:11px;">HOSTNAME</th>
        <th style="padding:10px 12px;text-align:left;font-size:11px;">IP ADDRESS</th>
        <th style="padding:10px 12px;text-align:center;font-size:11px;">STATE</th>
        <th style="padding:10px 12px;text-align:center;font-size:11px;">CPU</th>
        <th style="padding:10px 12px;text-align:center;font-size:11px;">MEMORY</th>
        <th style="padding:10px 12px;text-align:center;font-size:11px;">DISK</th>
        <th style="padding:10px 12px;text-align:center;font-size:11px;">UPTIME</th>
      </tr></thead>
      <tbody>{sum_rows}</tbody>
    </table></div>

    <div style="font-size:11px;font-weight:700;color:#1e3a5f;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:14px;">🖥️ Per-Host Details & Graphs</div>
    {host_cards}
    {emc_html}

    <div style="text-align:center;margin:22px 0 10px;">
      <a href="{dash}" style="background:#1e3a5f;color:#fff;text-decoration:none;
         padding:11px 26px;border-radius:6px;font-size:13px;font-weight:600;">
        🔗 Open Live Checkmk Dashboard</a></div>
    <div style="border-top:1px solid #dde5f0;padding-top:13px;margin-top:8px;
                text-align:center;font-size:11px;color:#aaa;">
      Auto-generated · Nakasero Hospital IT Department · {today}<br>
      Automated email — please do not reply</div>
  </div>
</div></body></html>"""


# ╔══════════════════════════════════════════════════════════╗
# ║  SECTION 6 — EMAIL + MAIN                              ║
# ╚══════════════════════════════════════════════════════════╝

def send_email(html_body, pdf_ok):
    today=datetime.datetime.now().strftime("%Y-%m-%d")
    msg=MIMEMultipart("mixed")
    msg["From"]=SMTP_FROM; msg["To"]=", ".join(RECIPIENTS)
    msg["Subject"]=f"Nakasero Hospital — Daily Infrastructure Report {today}"
    alt=MIMEMultipart("alternative")
    alt.attach(MIMEText(f"Nakasero Hospital Daily Infrastructure Report — {today}\nPDF attached.","plain"))
    alt.attach(MIMEText(html_body,"html"))
    msg.attach(alt)
    if pdf_ok and os.path.exists(PDF_PATH):
        with open(PDF_PATH,"rb") as f:
            part=MIMEBase("application","pdf"); part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition",
                        f'attachment; filename="NakaseroInfraReport_{today}.pdf"')
        msg.attach(part)
    ctx=ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_SERVER,SMTP_PORT,context=ctx) as srv:
        srv.login(SMTP_USER,SMTP_PASSWORD)
        srv.sendmail(SMTP_USER,RECIPIENTS,msg.as_string())
    print("  ✅ Email sent to all recipients.")


def main():
    ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] Nakasero Hospital — Starting daily infrastructure report...")

    print("  Fetching Checkmk hosts..."); raw_hosts=get_hosts()
    if not raw_hosts: print("  ERROR: No hosts."); sys.exit(1)
    print(f"  Found {len(raw_hosts)} host(s).")

    print("  Fetching services..."); services=get_services()
    print(f"  Found {len(services)} service(s).")

    print("  Enriching host data..."); hosts=enrich(raw_hosts,services)
    up=sum(1 for h in hosts if h["state_raw"]=="0")
    print(f"  UP:{up}  DOWN:{len(hosts)-up}  Total:{len(hosts)}")

    print("  Fetching Dell EMC PowerVault data...")
    emc=get_emc_data()
    if emc:
        print(f"  EMC: {emc.get('system',{}).get('health','?')} | "
              f"Disks:{len(emc.get('disks',[]))} | Vols:{len(emc.get('volumes',[]))}")
    else:
        print("  EMC: Could not connect — will be omitted from report.")

    print("  Generating graphs...")
    overview=make_overview(hosts)
    for h in hosts:
        make_host_graph(h)
        if h["datastores"]: make_datastore_graph(h)
    if emc: make_emc_disk_graph(emc)

    print("  Building HTML..."); html=build_html(hosts,overview,emc)
    print("  Building PDF..."); pdf_ok=build_pdf(hosts,overview,emc)
    print("  Sending email..."); send_email(html,pdf_ok)
    print(f"  Done at {datetime.datetime.now().strftime('%H:%M:%S')}.")

if __name__=="__main__": main()
