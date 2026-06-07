"""Web dashboard — same fleet state, rendered in a browser.

A tiny stdlib HTTP server (no Flask, no installs). Two routes:

  GET /            the HTML/CSS/JS dashboard (polls the API once a second)
  GET /api/state   current fleet state as JSON, derived from the event log

The browser does the drawing; this server just hands it derived state. Same
collector intelligence (staleness, burn, alerts) as the terminal dashboard —
only the renderer differs. The page shows an animated "floor" where each agent
is an avatar that moves around while it works.
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .collector import EventLog, FleetState, derive


def state_to_json(state: FleetState, budget_usd: float) -> dict:
    """Flatten FleetState into the shape the browser expects."""
    teams: dict[str, list[dict]] = {}
    for a in state.agents:
        hb = a.hb
        teams.setdefault(hb.team, []).append({
            "agent_id": hb.agent_id,
            "role": hb.role,
            "model": hb.model,
            "status": hb.status.value,
            "glyph": hb.status.glyph,
            "task": hb.task or hb.step,
            "progress": hb.progress,
            "tokens": hb.tokens,
            "context_used": hb.context_used,
            "spend_usd": hb.spend_usd,
            "retry_count": hb.retry_count,
            "error": hb.error,
            "blocked_on": hb.blocked_on,
            "age_s": round(a.age_s, 1),
            "stale": a.stale,
            "burn": round(a.burn_usd_per_min, 2),
        })
    return {
        "now": state.now,
        "counts": {s.value: n for s, n in state.counts.items()},
        "total_spend": round(state.total_spend, 2),
        "budget": budget_usd,
        "burn": round(state.burn_usd_per_min, 2),
        "alerts": state.alerts,
        "teams": teams,
    }


def _make_handler(log_path: str, budget_usd: float):
    log = EventLog(log_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence per-request stderr spam
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/state"):
                state = derive(log.read_all(), budget_usd=budget_usd)
                body = json.dumps(state_to_json(state, budget_usd)).encode()
                self._send(200, body, "application/json")
            elif self.path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def serve(log_path: str, host: str = "127.0.0.1", port: int = 8000,
          budget_usd: float = 20.0, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer((host, port), _make_handler(log_path, budget_usd))
    url = f"http://{host}:{port}/"
    print(f"Mission Control web dashboard → {url}  (Ctrl-C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# ── single-file frontend: HTML + CSS + JS, polls /api/state once a second ────
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mission Control · house-elf</title>
<style>
  :root {
    --bg:#0b0f14; --panel:#121821; --line:#1f2a37; --txt:#cdd6e0; --dim:#6b7a8d;
    --run:#3fb950; --wait:#d29922; --idle:#6b7a8d; --done:#39c5cf;
    --fail:#f85149; --retry:#bc8cff;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--bg); color:var(--txt);
    font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  .wrap { max-width:1000px; margin:0 auto; padding:20px; }
  header {
    display:flex; justify-content:space-between; align-items:baseline;
    border-bottom:1px solid var(--line); padding-bottom:10px; margin-bottom:14px;
  }
  header h1 { font-size:16px; margin:0; letter-spacing:.5px; }
  header .meta { color:var(--dim); font-size:13px; }
  .alertcount.has { color:var(--fail); font-weight:bold; }
  .summary {
    display:flex; flex-wrap:wrap; gap:14px; align-items:center;
    background:var(--panel); border:1px solid var(--line); border-radius:8px;
    padding:12px 14px; margin-bottom:8px;
  }
  .chip { display:flex; align-items:center; gap:5px; font-size:13px; }
  .glyph { font-size:15px; }
  .RUNNING .glyph,.c-RUNNING{color:var(--run)} .WAITING .glyph,.c-WAITING{color:var(--wait)}
  .IDLE .glyph,.c-IDLE{color:var(--idle)} .DONE .glyph,.c-DONE{color:var(--done)}
  .FAILED .glyph,.c-FAILED{color:var(--fail)} .RETRYING .glyph,.c-RETRYING{color:var(--retry)}
  .spend {
    display:flex; align-items:center; gap:12px; background:var(--panel);
    border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin-bottom:16px;
  }
  .bar { flex:1; height:10px; background:#0b1118; border-radius:6px; overflow:hidden; }
  .bar > i { display:block; height:100%; background:var(--run); transition:width .4s; }
  .bar.warn > i{background:var(--wait)} .bar.crit > i{background:var(--fail)}
  .burn { color:var(--dim); white-space:nowrap; }

  /* ── the animated floor ── */
  .floor {
    position:relative; height:clamp(320px,46vh,520px); margin-bottom:18px;
    background:
      linear-gradient(#0e141c,#0c1117),
      repeating-linear-gradient(0deg,transparent,transparent 39px,#121a24 39px,#121a24 40px),
      repeating-linear-gradient(90deg,transparent,transparent 39px,#121a24 39px,#121a24 40px);
    border:1px solid var(--line); border-radius:12px; overflow:hidden;
  }
  .zone {
    position:absolute; font-size:12px; letter-spacing:.6px; color:var(--dim);
    text-transform:uppercase; pointer-events:none; opacity:.55;
  }
  .zone.wait{left:10px; top:8px} .zone.done{right:10px; top:8px}
  .zone.fail{left:10px; bottom:6px} .zone.run{left:50%; top:8px; transform:translateX(-50%)}
  .zwait{position:absolute; left:0; top:0; bottom:0; width:120px;
    background:linear-gradient(90deg,rgba(210,153,34,.07),transparent)}
  .zdone{position:absolute; right:0; top:0; bottom:0; width:120px;
    background:linear-gradient(270deg,rgba(57,197,207,.07),transparent)}
  .zfail{position:absolute; left:0; right:0; bottom:0; height:70px;
    background:linear-gradient(0deg,rgba(248,81,73,.08),transparent)}

  .sprite {
    position:absolute; left:0; top:0; width:72px; text-align:center;
    will-change:transform; pointer-events:none;
  }
  .sprite .avatar {
    font-size:30px; line-height:46px; width:46px; height:46px; margin:0 auto;
    border-radius:50%; border:2px solid var(--idle); background:#0d1420;
    box-shadow:0 2px 8px rgba(0,0,0,.4); transition:border-color .3s,filter .3s,opacity .3s;
  }
  .sprite .badge {
    position:absolute; top:-2px; left:50%; margin-left:10px; font-size:14px;
    width:18px; height:18px; line-height:18px; border-radius:50%;
    background:#0d1420; border:1px solid var(--line);
  }
  .sprite .label {
    font-size:11px; color:var(--txt); margin-top:2px; white-space:nowrap;
    text-shadow:0 1px 2px #000;
  }
  .sprite .task { font-size:10px; color:var(--dim); white-space:nowrap;
    overflow:hidden; max-width:96px; text-overflow:ellipsis; margin:0 auto; }
  .sprite .pbar { width:42px; height:4px; margin:1px auto 0; background:#0b1118;
    border-radius:3px; overflow:hidden; }
  .sprite .pbar > i { display:block; height:100%; width:0; background:var(--run); }
  .st-RUNNING  .avatar{border-color:var(--run);  box-shadow:0 0 10px rgba(63,185,80,.5)}
  .st-WAITING  .avatar{border-color:var(--wait); opacity:.85}
  .st-IDLE     .avatar{border-color:var(--idle); opacity:.7}
  .st-DONE     .avatar{border-color:var(--done)}
  .st-RETRYING .avatar{border-color:var(--retry)}
  .st-FAILED   .avatar{border-color:var(--fail); filter:grayscale(.8); opacity:.85}
  .st-RUNNING  .pbar > i{background:var(--run)}
  .st-RETRYING .pbar > i{background:var(--retry)}
  .st-DONE     .pbar > i{background:var(--done)}

  .team h2 {
    font-size:13px; color:var(--dim); margin:18px 0 6px; font-weight:600;
    text-transform:uppercase; letter-spacing:.6px;
  }
  table { width:100%; border-collapse:collapse; }
  td { padding:7px 8px; border-bottom:1px solid var(--line); vertical-align:middle; }
  .ag-status { width:96px; font-weight:600; }
  .ag-name { font-weight:600; }
  .ag-task { color:var(--dim); }
  .pbar2 { width:90px; height:8px; background:#0b1118; border-radius:5px; overflow:hidden; }
  .pbar2 > i { display:block; height:100%; background:var(--run); }
  .tag { font-size:12px; padding:1px 7px; border-radius:10px; white-space:nowrap;
    border:1px solid var(--line); }
  .tag.fail{color:var(--fail);border-color:#3a1d1d} .tag.warn{color:var(--wait);border-color:#3a311d}
  .tag.retry{color:var(--retry)} .tag.wait{color:var(--wait)}
  .num { color:var(--dim); font-size:13px; text-align:right; }
  .alerts { margin-top:18px; background:var(--panel); border:1px solid var(--line);
    border-radius:8px; padding:12px 14px; }
  .alerts h2 { font-size:13px; margin:0 0 8px; color:var(--dim); letter-spacing:.6px; }
  .alerts li { list-style:none; padding:3px 0; }
  .alerts.nominal { color:var(--run); }
  .alert-crit{color:var(--fail)} .alert-warn{color:var(--wait)}
  footer { color:var(--dim); font-size:12px; margin-top:18px; text-align:center; }
  .dot { color:var(--run); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>MISSION CONTROL · house-elf</h1>
    <span class="meta"><span class="dot">●</span> <span id="clock"></span>
      &nbsp; <span id="alertcount" class="alertcount"></span></span>
  </header>
  <div class="summary" id="summary"></div>
  <div class="spend" id="spend"></div>

  <div class="floor" id="floor">
    <div class="zwait"></div><div class="zdone"></div><div class="zfail"></div>
    <div class="zone wait">⏸ waiting</div>
    <div class="zone run">● working floor</div>
    <div class="zone done">✔ done</div>
    <div class="zone fail">✖ failed</div>
  </div>

  <div id="teams"></div>
  <div class="alerts" id="alerts"></div>
  <footer>avatars roam while working · auto-refreshing every second · pure stdlib backend</footer>
</div>
<script>
const GLYPH = {RUNNING:"●",WAITING:"⏸",IDLE:"○",DONE:"✔",FAILED:"✖",RETRYING:"◐"};
const ORDER = ["RUNNING","WAITING","IDLE","DONE","FAILED","RETRYING"];
// avatar per role (falls back to a robot)
const AVATAR = {planner:"🧭",scanner:"🔍",coder:"👩‍💻",tester:"🧪",
  reviewer:"🔎",ops:"🛠️",ops2:"📟"};
function avatarFor(a){ return AVATAR[a.role] || "🤖"; }

function fmtTokens(n){ if(n>=1e6)return (n/1e6).toFixed(1)+"M";
  if(n>=1e3)return Math.round(n/1e3)+"k"; return ""+n; }
function fmtAge(s){ if(s<1)return"now"; if(s<60)return Math.round(s)+"s ago";
  if(s<3600)return Math.round(s/60)+"m ago"; return (s/3600).toFixed(1)+"h ago"; }

/* ───────────────── animated floor ───────────────── */
const floorEl = document.getElementById("floor");
let FW = 800, FH = 380, PAD = 30;
function measure(){ FW = floorEl.clientWidth; FH = floorEl.clientHeight; }
window.addEventListener("resize", measure); measure();

const sprites = {};   // agent_id -> {x,y,heading,phase,el,avatar,badge,label,task,bar,data,target}

function makeSprite(a){
  const el = document.createElement("div");
  el.className = "sprite st-"+a.status;
  el.innerHTML =
    `<div class="badge">${a.glyph}</div>
     <div class="avatar">${avatarFor(a)}</div>
     <div class="pbar"><i></i></div>
     <div class="label"></div>
     <div class="task"></div>`;
  floorEl.appendChild(el);
  return {
    x: PAD + Math.random()*(FW-2*PAD), y: PAD + Math.random()*(FH-2*PAD),
    heading: Math.random()*Math.PI*2, phase: Math.random()*Math.PI*2,
    el, avatar: el.querySelector(".avatar"), badge: el.querySelector(".badge"),
    label: el.querySelector(".label"), task: el.querySelector(".task"),
    bar: el.querySelector(".pbar > i"), data: a, target: null, st: "",
  };
}

// where non-roaming agents settle; counters stack them into neat rows
function zoneTarget(a, z){
  const st = a.status;
  if(st==="WAITING")  { const i=z.WAITING++; return {x:60,      y:64+i*72}; }
  if(st==="DONE")     { const i=z.DONE++;    return {x:FW-60,   y:64+i*62}; }
  if(st==="FAILED")   { const i=z.FAILED++;  return {x:80+i*86, y:FH-44};   }
  return null;  // RUNNING roams; IDLE/RETRYING stay put
}

function syncSprites(s){
  measure();
  const seen = new Set();
  const z = {WAITING:0, DONE:0, FAILED:0};
  const all = [];
  for(const t of Object.keys(s.teams)) for(const a of s.teams[t]) all.push(a);
  for(const a of all){
    seen.add(a.agent_id);
    let sp = sprites[a.agent_id];
    if(!sp){ sp = makeSprite(a); sprites[a.agent_id] = sp; }
    sp.data = a;
    sp.target = zoneTarget(a, z);
  }
  for(const id of Object.keys(sprites)){
    if(!seen.has(id)){ sprites[id].el.remove(); delete sprites[id]; }
  }
}

const SPEED = 48;   // px/sec while wandering
let last = performance.now();
function frame(now){
  let dt = (now - last)/1000; last = now; if(dt>0.1) dt = 0.1;
  for(const id in sprites){
    const sp = sprites[id], a = sp.data, st = a.status;

    if(st === "RUNNING"){
      // gentle random walk, bouncing off the walls
      sp.heading += (Math.random()-0.5) * 2.4 * dt;
      let nx = sp.x + Math.cos(sp.heading)*SPEED*dt;
      let ny = sp.y + Math.sin(sp.heading)*SPEED*dt;
      if(nx<PAD || nx>FW-PAD){ sp.heading = Math.PI - sp.heading;
        nx = Math.max(PAD, Math.min(FW-PAD, nx)); }
      if(ny<PAD || ny>FH-PAD){ sp.heading = -sp.heading;
        ny = Math.max(PAD, Math.min(FH-PAD, ny)); }
      sp.x = nx; sp.y = ny;
    } else if(sp.target){
      // ease toward the agent's zone slot
      sp.x += (sp.target.x - sp.x) * Math.min(1, dt*4);
      sp.y += (sp.target.y - sp.y) * Math.min(1, dt*4);
    }

    // little flourishes: bob while running, shake while retrying
    let ox = 0, oy = 0;
    if(st === "RUNNING")  oy = Math.sin(now/180 + sp.phase) * 3;
    if(st === "RETRYING") ox = Math.sin(now/45) * 4;
    sp.el.style.transform =
      `translate(${(sp.x - 36 + ox).toFixed(1)}px, ${(sp.y - 23 + oy).toFixed(1)}px)`;

    if(sp.st !== st){ sp.st = st; sp.el.className = "sprite st-"+st; }
    sp.badge.textContent = a.glyph;
    sp.bar.style.width = (a.progress*100).toFixed(0) + "%";
    sp.label.textContent = a.agent_id;
    sp.task.textContent = (st==="RUNNING" || st==="RETRYING") ? (a.task||"") : "";
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

/* ───────────────── panels (summary / spend / roster / alerts) ───────────────── */
function agentTag(a){
  if(a.stale) return `<span class="tag fail">⚠ stale</span>`;
  if(a.status==="FAILED"&&a.error) return `<span class="tag fail">${a.error}</span>`;
  if(a.status==="RETRYING") return `<span class="tag retry">↻ ${a.retry_count}</span>`;
  if(a.status==="WAITING"&&a.blocked_on.length)
    return `<span class="tag wait">⤷ ${a.blocked_on.join(", ")}</span>`;
  return "";
}

function render(s){
  document.getElementById("clock").textContent =
    new Date(s.now*1000).toLocaleTimeString();
  const ac = document.getElementById("alertcount");
  ac.textContent = "▲ "+s.alerts.length+" alerts";
  ac.className = "alertcount"+(s.alerts.length?" has":"");

  document.getElementById("summary").innerHTML = ORDER.map(st=>
    `<span class="chip ${st}"><span class="glyph">${GLYPH[st]}</span>
     ${s.counts[st]||0} ${st.toLowerCase()}</span>`).join("");

  const frac = s.budget ? Math.min(1,s.total_spend/s.budget) : 0;
  const cls = frac>0.9?"crit":frac>0.6?"warn":"";
  document.getElementById("spend").innerHTML =
    `<span>SPEND $${s.total_spend.toFixed(2)}${s.budget?` / $${s.budget.toFixed(2)}`:""}</span>
     <span class="bar ${cls}"><i style="width:${(frac*100).toFixed(0)}%"></i></span>
     <span class="burn">🔥 ${s.burn.toFixed(2)} $/min</span>`;

  const teams = Object.keys(s.teams).sort();
  document.getElementById("teams").innerHTML = teams.map(t=>{
    const rows = s.teams[t].map(a=>`
      <tr class="${a.status}">
        <td class="ag-status c-${a.status}"><span class="glyph">${a.glyph}</span> ${a.status}</td>
        <td class="ag-name">${avatarFor(a)} ${a.agent_id}</td>
        <td class="ag-task">${a.task||"—"}</td>
        <td><span class="pbar2"><i style="width:${(a.progress*100).toFixed(0)}%"></i></span></td>
        <td class="num">${fmtTokens(a.tokens)}</td>
        <td class="num">${fmtAge(a.age_s)}</td>
        <td>${agentTag(a)}</td>
      </tr>`).join("");
    return `<div class="team"><h2>◢ ${t} &nbsp;<span style="opacity:.6">
      (${s.teams[t].length})</span></h2><table>${rows}</table></div>`;
  }).join("");

  const box = document.getElementById("alerts");
  if(!s.alerts.length){
    box.className="alerts nominal";
    box.innerHTML="<h2>ALERTS</h2><li>▲ no alerts — fleet nominal</li>";
  } else {
    box.className="alerts";
    box.innerHTML="<h2>▲ ALERTS</h2>"+s.alerts.map(a=>{
      const crit = "✖🔥💸".includes(a[0]);
      return `<li class="${crit?'alert-crit':'alert-warn'}">${a}</li>`;
    }).join("");
  }
}

async function tick(){
  try{
    const r = await fetch("/api/state");
    const s = await r.json();
    syncSprites(s);
    render(s);
  } catch(e){ /* server restarting; try again next tick */ }
}
tick(); setInterval(tick, 1000);
</script>
</body>
</html>
"""
