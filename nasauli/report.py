"""Interactive HTML report for one processed flight (Plotly.js from a CDN).

Built from processed/<flight>/ (tables + metadata.json), never from raw data. The page works on its own
and inside the website, where it is embedded in an iframe: in that mode it hides its own header, follows
the site's light/dark theme, and reports its height to the parent page.
"""

from __future__ import annotations

import html
import json
import math

import numpy as np
import pandas as pd

from .localtime import fmt, to_local

MAX_PLOT_HZ = 10.0


def _arr(s: pd.Series, nd: int = 2) -> list:
    v = pd.to_numeric(s, errors="coerce").round(nd).to_numpy(dtype=float)
    return [None if math.isnan(x) else (int(x) if nd == 0 else float(x)) for x in v]


def _fmt(x, unit="", nd=1):
    if x is None:
        return "–"
    return f"{x:,.{nd}f}{unit}"


def plot_data(df: pd.DataFrame, wind: pd.DataFrame | None, hwas: pd.DataFrame | None = None) -> dict:
    rate = (len(df) - 1) / max(df["elapsed_s"].iloc[-1] - df["elapsed_s"].iloc[0], 1e-9)
    step = max(1, round(rate / MAX_PLOT_HZ))
    d = df.iloc[::step]
    data = {
        "t": _arr(d["elapsed_s"], 2),
        "local": [x.strftime("%H:%M:%S.%f")[:-4] if pd.notna(x) else None for x in d["time_local"]],
        "E": _arr(d["east_m"], 1), "N": _arr(d["north_m"], 1),
        "alt": _arr(d["alt_rel_m"], 2), "gs": _arr(d["groundspeed_m_s"], 2), "vz": _arr(d["climb_m_s"], 2),
        "roll": _arr(d["roll_deg"], 1), "pitch": _arr(d["pitch_deg"], 1),
        "V": _arr(d["batt_voltage_v"], 2), "I": _arr(d["batt_current_a"], 2),
        "mah": _arr(d["batt_consumed_mah"], 0),
        "m1": _arr(d["motor1_us"], 0), "m2": _arr(d["motor2_us"], 0),
        "m3": _arr(d["motor3_us"], 0), "m4": _arr(d["motor4_us"], 0),
        "vx": _arr(d["vib_x_m_s2"], 2), "vy": _arr(d["vib_y_m_s2"], 2), "vzb": _arr(d["vib_z_m_s2"], 2),
        "ev": _arr(d["ekf_vel_var"], 3), "eph": _arr(d["ekf_pos_horiz_var"], 3),
        "epv": _arr(d["ekf_pos_vert_var"], 3), "ec": _arr(d["ekf_compass_var"], 3),
        "sats": _arr(d["gps_sats"], 0), "hdop": _arr(d["gps_hdop"], 2), "fixt": _arr(d["gps_fix_type"], 0),
        "mode": [m if isinstance(m, str) else "—" for m in d["flight_mode"].astype(object)],
        "btemp": _arr(d["batt_temp_c"], 2),
    }
    w = None
    if wind is not None and len(wind):
        w = {"t": _arr(wind["elapsed_s"], 2), "spd": _arr(wind["wind_speed_m_s"], 1),
             "dir": _arr(wind["wind_dir_deg"], 0), "temp": _arr(wind["temperature_c"], 1),
             "mean_spd": round(float(wind["wind_speed_m_s"].mean()), 1),
             "mean_from": round(circmean(wind["wind_dir_deg"])),
             "mean_from_name": compass(circmean(wind["wind_dir_deg"]))}
    h = None
    if hwas is not None and len(hwas):
        h = {"t": _arr(hwas["elapsed_s"], 1), "spd": _arr(hwas["wind_speed_m_s"], 2),
             "gust": _arr(hwas["gust_m_s"].where(hwas["gust_m_s"] > 0), 2), "dir": _arr(hwas["wind_dir_deg"], 0),
             "temp": _arr(hwas["temperature_c"], 2), "rh": _arr(hwas["humidity_pct"], 0),
             "mean_spd": round(float(hwas["wind_speed_m_s"].mean()), 1),
             "mean_from": round(circmean(hwas["wind_dir_deg"])),
             "mean_from_name": compass(circmean(hwas["wind_dir_deg"]))}
    return {"D": data, "W": w, "H": h}


def compass(deg: float) -> str:
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((deg % 360) / 22.5 + 0.5) % 16]


def circmean(deg: pd.Series) -> float:
    r = np.radians(deg.dropna())
    return float(np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360)


def stat_tiles(meta: dict, wind: pd.DataFrame | None,
               hwas: pd.DataFrame | None = None) -> list[tuple[str, str, str]]:
    """(label, value, detail) summary tiles, shared by the report and the website's flight pages."""
    sm = meta["summary"]
    tiles = []
    if sm.get("airborne_s") is not None:
        tiles.append(("Flight", f"≈{sm['airborne_s']:.0f} s airborne",
                      f"takeoff ≈{sm['takeoff_s']:.0f} s, landed ≈{sm['landing_s']:.0f} s"
                      if sm.get("landed_in_log") else f"takeoff ≈{sm['takeoff_s']:.0f} s, log ends airborne"))
    else:
        tiles.append(("Flight", "not airborne", f"{sm['duration_s']:.0f} s logged"))
    dur: dict[str, float] = {}
    for a, b, m in sm["modes"]:
        if m != "—":
            dur[m] = dur.get(m, 0.0) + (b - a)
    if dur:
        main_mode = max(dur, key=dur.get)
        sub = f"RTL from {sm['rtl_s']:.1f} s" if sm.get("rtl_s") is not None else ", ".join(dur)
        tiles.append(("Mission", main_mode + (f", {sm['mission_items']} WPs" if sm.get("mission_items") else ""), sub))
    if sm.get("cruise_speed_m_s") is not None:
        tiles.append(("Cruise", f"{sm['cruise_alt_m']:.0f} m, {sm['cruise_speed_m_s']:.1f} m/s",
                      "relative alt, groundspeed"))
    tiles.append(("Max range", _fmt(sm.get("max_range_m"), " m", 0),
                  f"from home; max alt {_fmt(sm.get('max_alt_rel_m'), ' m')}"))
    if sm.get("batt_consumed_mah") is not None:
        bsub = f"{_fmt(sm.get('batt_v_start'), ' V')} → {_fmt(sm.get('batt_v_end'), ' V')}"
        bsub += " at rest" if sm.get("batt_v_end_at_rest") else " (end not at rest)"
        if sm.get("cruise_current_a") is not None:
            bsub += f", ≈{sm['cruise_current_a']:.0f} A cruise"
        tiles.append(("Battery", _fmt(sm.get("batt_consumed_mah"), " mAh", 0), bsub))
    g = sm.get("gps") or {}
    if g.get("fix"):
        main = g["fix"][0] if g["fix"][0]["pct"] >= 50 else max(g["fix"], key=lambda f: f["pct"])
        rest = [f for f in g["fix"] if f is not main]
        gsub = (f"{main['accuracy']} · " if main["accuracy"] else "") + f"{main['pct']:.0f}% of flight"
        if rest:
            gsub += "; " + ", ".join(f"{f['name']} {f['pct']:.0f}%" for f in rest)
        tiles.append(("GPS", main["name"], gsub))
    elif g.get("sats_min") is not None:
        tiles.append(("GPS", "fix type not logged", f"≥{g['sats_min']:.0f} satellites"))
    if wind is not None and len(wind):
        d = circmean(wind["wind_dir_deg"])
        tiles.append(("Wind (wind drone)", f"{wind['wind_speed_m_s'].mean():.1f} m/s mean",
                      f"max {wind['wind_speed_m_s'].max():.1f} m/s, from ≈{d:.0f}° ({compass(d)})"))
    elif meta.get("wind"):
        tiles.append(("Wind (wind drone)", "none", "no wind-drone log covers this flight"))
    if hwas is not None and len(hwas):
        d = circmean(hwas["wind_dir_deg"])
        gust = hwas["gust_m_s"].max()
        tiles.append(("Wind (HWAS)", f"{hwas['wind_speed_m_s'].mean():.1f} m/s mean",
                      (f"gusts to {gust:.1f} m/s, " if gust > 0 else "") + f"from ≈{d:.0f}° ({compass(d)})"))
        tiles.append(("Air (HWAS)", f"{hwas['temperature_c'].mean():.1f} °C",
                      f"{hwas['humidity_pct'].mean():.0f}% humidity"))
    return tiles


def _stats_html(tiles) -> str:
    return "\n".join(
        f'<div class="stat"><div class="k">{html.escape(k)}</div><div class="v">{html.escape(v)}</div>'
        f'<div class="s">{html.escape(sub)}</div></div>' for k, v, sub in tiles)


def _notes_html(checks: list[dict]) -> str:
    if not checks:
        return ""
    n_warn = sum(c["level"] == "warn" for c in checks)
    items = "\n".join(
        f'<p class="{c["level"]}"><strong>{html.escape(c["title"])}.</strong> {html.escape(c["detail"])}</p>'
        for c in checks)
    n_info = len(checks) - n_warn
    summary = (f"Things to check in this log ({n_warn} warning{'s' if n_warn != 1 else ''}, "
               f"{n_info} note{'s' if n_info != 1 else ''})")
    return f'<details class="notes" open>\n<summary>{summary}</summary>\n{items}\n</details>'


def _with_home(p: dict, sm: dict) -> dict:
    g = sm.get("gps") or {}
    p["D"]["home_msl"] = round(g["home_msl_m"], 1) if g.get("home_msl_m") is not None else None
    p["D"]["home_src"] = g.get("home_msl_source")
    return p


def title_for(meta: dict) -> str:
    return f"{meta['platform']['label']} flight, {fmt(meta['summary']['start_utc'], '%d %b %Y %H:%M %Z')}"


def render(meta: dict, flight: pd.DataFrame, wind: pd.DataFrame | None,
           downloads: dict[str, str], back_href: str = "../index.html", hwas: pd.DataFrame | None = None) -> str:
    sm = meta["summary"]
    start = to_local(sm["start_utc"])
    end = to_local(sm["end_utc"])
    has_modes = any(m[2] != "—" for m in sm["modes"])
    sub = (f"Session <code>{html.escape(sm['session_id'])}</code>, {start:%d %b %Y}, "
           f"{start:%H:%M:%S} to {end:%H:%M:%S} {start:%Z} ({sm['duration_s']:.0f} s at {sm['sample_rate_hz']:.0f} Hz). "
           + ("Shaded bands are AUTO mode. " if has_modes else "")
           + "Zoom any time plot and the others follow; the time range you zoom to is highlighted on the ground track.")
    dl = " · ".join(f'<a href="{html.escape(href)}">{html.escape(label)}</a>' for label, href in downloads.items())
    footer = (f"Generated from <code>{html.escape(meta['source']['file'])}</code> "
              f"({html.escape(meta['header'].get('format', ''))}) by the nasauli pipeline "
              f"v{meta['pipeline_version']}, reader <code>{meta['reader']}</code>. Time axis is "
              f"<code>elapsed_s</code>; hover times are GPS-corrected Mountain Time.")
    wind_section = (
        '<section data-plot="p-wind"><h2>Wind</h2><p class="cap">Speed (left axis): wind drone (line) and HWAS weather '
        'station (steps, one reading every 30 s; gusts dotted). Direction the wind comes from (right axis, dots). '
        'Matched to this flight on GPS time.</p><div id="p-wind" class="plot"></div></section>'
        '<section data-plot="p-temp"><h2>Temperatures</h2><p class="cap">Air (HWAS weather station), wind-drone sensor, '
        'and battery (Tarot telemetry), °C</p><div id="p-temp" class="plot"></div></section>')
    out = TEMPLATE
    for k, v in {
        "__TITLE__": html.escape(title_for(meta)), "__H1__": html.escape(f"{meta['platform']['label']} flight"),
        "__BACK__": html.escape(back_href),
        "__SUB__": sub, "__STATS__": _stats_html(stat_tiles(meta, wind, hwas)), "__NOTES__": _notes_html(meta["checks"]),
        "__WIND__": wind_section, "__DOWNLOADS__": dl, "__FOOTER__": footer,
        "__MODES__": json.dumps(sm["modes"], separators=(",", ":")),
        "__DATA__": json.dumps(_with_home(plot_data(flight, wind, hwas), sm), separators=(",", ":")),
    }.items():
        out = out.replace(k, v)
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
:root{--bg:#EEF2F5;--panel:#FFFFFF;--ink:#1C2733;--muted:#5B6B7B;--rule:#C9D3DC;--grid:#E1E7EC;
--c1:#2F6690;--c2:#C77D19;--c3:#3A7D6B;--c4:#B23A48;--warn-bg:#FBF1E3;--info-bg:#E9EFF5;--band:rgba(47,102,144,.06)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141A21;--panel:#1B232C;--ink:#DCE3EA;--muted:#8C9AA8;--rule:#2E3945;--grid:#26303A;--c1:#6FA8D6;--c2:#E0A04A;--c3:#6CB8A0;--c4:#E07480;--warn-bg:#2A2419;--info-bg:#1E2833;--band:rgba(111,168,214,.08)}}
:root[data-theme="dark"]{--bg:#141A21;--panel:#1B232C;--ink:#DCE3EA;--muted:#8C9AA8;--rule:#2E3945;--grid:#26303A;--c1:#6FA8D6;--c2:#E0A04A;--c3:#6CB8A0;--c4:#E07480;--warn-bg:#2A2419;--info-bg:#1E2833;--band:rgba(111,168,214,.08)}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums}
main{max-width:1200px;margin:0 auto;padding:28px 16px 48px}
a{color:var(--c1)}
h1{font-size:1.6rem;font-weight:600;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--muted);margin:0 0 18px}
code{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:.88em;overflow-wrap:anywhere}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 18px}
.stat{background:var(--panel);border:1px solid var(--rule);border-radius:4px;padding:10px 12px}
.stat .k{color:var(--muted);font-size:.8rem}.stat .v{font-size:1.25rem;font-weight:500}.stat .s{color:var(--muted);font-size:.8rem}
details.notes{background:var(--warn-bg);border-left:3px solid var(--c2);padding:10px 16px;margin:0 0 20px;border-radius:2px}
details.notes summary{cursor:pointer;font-weight:600}
details.notes p{margin:8px 0 0;max-width:90ch}
details.notes p.info{color:var(--muted)}
.top{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:14px;margin-bottom:14px}
@media (max-width:820px){.top{grid-template-columns:1fr}}
section{background:var(--panel);border:1px solid var(--rule);border-radius:4px;padding:12px 12px 4px}
.stack section{margin-bottom:14px}
h2{font-size:1rem;font-weight:600;margin:0 2px 2px}
.cap{color:var(--muted);font-size:.85rem;margin:0 2px 4px}
.plot{width:100%;height:230px}
#p-track{height:440px}
.bar{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin:0 0 10px}
.readout{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:.85rem;color:var(--muted)}
button{font:inherit;font-size:.85rem;color:var(--ink);background:var(--panel);border:1px solid var(--rule);border-radius:3px;padding:4px 10px;cursor:pointer}
button:focus-visible{outline:2px solid var(--c1);outline-offset:2px}
.downloads{margin:0 0 18px;font-size:.9rem}
footer{color:var(--muted);font-size:.85rem;margin-top:10px}
html.embed .head,html.embed footer{display:none}
html.embed body{background:transparent}
html.embed main{padding:0;max-width:none}
</style>
</head>
<body>
<main>
<div class="head">
<p class="downloads"><a href="__BACK__">← All flights</a></p>
<h1>__H1__</h1>
<p class="sub">__SUB__</p>
<div class="stats">
__STATS__
</div>
__NOTES__
<p class="downloads">Download: __DOWNLOADS__</p>
</div>
<div class="bar"><span class="readout" id="readout">Hover a plot to read values.</span><button id="reset" type="button">Reset zoom</button></div>
<div class="top">
<section><h2>Ground track</h2><p class="cap">Meters from home, equal scale. Color is time.</p><div id="p-track" class="plot"></div></section>
<div class="stack">
<section data-plot="p-alt"><h2>Altitude</h2><p class="cap" id="alt-cap">Above takeoff</p><div id="p-alt" class="plot"></div></section>
<section data-plot="p-gs"><h2>Groundspeed and climb rate</h2><div id="p-gs" class="plot"></div></section>
</div>
</div>
<section style="margin-bottom:14px"><h2>3D trajectory</h2><p class="cap">Meters from home; height relative to takeoff, vertical scale exaggerated. Drag to rotate, scroll to zoom.</p><div id="p-3d" class="plot" style="height:520px"></div></section>
<div class="stack">
__WIND__
<section data-plot="p-att"><h2>Attitude</h2><p class="cap">Roll and pitch in degrees.</p><div id="p-att" class="plot"></div></section>
<section data-plot="p-bat"><h2>Battery</h2><p class="cap">Voltage (left axis), current (right axis)</p><div id="p-bat" class="plot"></div></section>
<section data-plot="p-mot"><h2>Motor outputs</h2><p class="cap">SRV1–4 PWM, µs</p><div id="p-mot" class="plot"></div></section>
<section data-plot="p-vib"><h2>Vibration</h2><p class="cap">m/s²</p><div id="p-vib" class="plot"></div></section>
<section data-plot="p-gps"><h2>GPS</h2><p class="cap">Fix type (left axis; RTK Fixed: cm-level and RTK Float: ~0.2–1 m, both relative to the base; DGPS ~0.5–2 m; 3D ~2–5 m) and satellites (right axis)</p><div id="p-gps" class="plot"></div></section>
<section data-plot="p-ekf"><h2>EKF variances</h2><p class="cap">Dotted line: ArduPilot's default failsafe threshold (0.8)</p><div id="p-ekf" class="plot"></div></section>
</div>
<footer>__FOOTER__</footer>
</main>
<script>
const MODES=__MODES__;
const P=__DATA__;const D=P.D,W=P.W,H=P.H;
// Embedded in the website: hide the page header, follow the site's theme, size the iframe to the content.
const EMBED=window.self!==window.top;
if(EMBED){document.documentElement.classList.add('embed');
  try{const pr=window.parent.document.documentElement,sync=()=>{const t=pr.dataset.theme;if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;else delete document.documentElement.dataset.theme;
      document.body.style.background=getComputedStyle(window.parent.document.body).backgroundColor;};
    sync();new MutationObserver(sync).observe(pr,{attributes:true,attributeFilter:['data-theme']});
    const fit=()=>{const m=document.querySelector('main');if(window.frameElement&&m)window.frameElement.style.height=Math.ceil(m.getBoundingClientRect().height)+8+'px';};
    new ResizeObserver(fit).observe(document.querySelector('main'));window.addEventListener('load',fit);}catch(e){}}
// Drop panels whose data this log format doesn't have.
const has=(...ks)=>ks.some(k=>(D[k]||[]).some(v=>v!==null&&v!==0));
const AVAIL={'p-alt':has('alt'),'p-gs':has('gs','vz'),'p-att':has('roll','pitch'),'p-bat':has('V','I'),'p-mot':has('m1','m2','m3','m4'),
  'p-vib':has('vx','vy','vzb'),'p-gps':has('fixt'),'p-ekf':has('ev','eph','epv','ec'),'p-wind':!!(W||H),
  'p-temp':has('btemp')||!!(W&&W.temp.some(v=>v!==null))||!!(H&&H.temp.some(v=>v!==null))};
const FIXN={0:'No GPS',1:'No fix',2:'2D',3:'3D',4:'DGPS',5:'RTK Float',6:'RTK Fixed',7:'Static',8:'PPP'};
document.querySelectorAll('section[data-plot]').forEach(s=>{if(!AVAIL[s.dataset.plot])s.remove();});
const TS=Object.keys(AVAIL).filter(k=>AVAIL[k]);
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const cfg={responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']};
function axis(extra){return Object.assign({gridcolor:css('--grid'),zerolinecolor:css('--rule'),linecolor:css('--rule'),tickfont:{color:css('--muted')},title:{font:{color:css('--muted')}}},extra||{});}
function base(extra){
  const shapes=MODES.filter(m=>m[2]==='AUTO').map(m=>({type:'rect',xref:'x',yref:'paper',x0:m[0],x1:m[1],y0:0,y1:1,fillcolor:css('--band'),line:{width:0},layer:'below'}));
  const span=D.t[D.t.length-1]-D.t[0];
  const ann=MODES.filter(m=>m[1]-m[0]>span/12&&m[2]!=='—').map(m=>({x:(m[0]+m[1])/2,y:1,xref:'x',yref:'paper',yanchor:'top',text:m[2],showarrow:false,font:{size:10,color:css('--muted')}}));
  return Object.assign({margin:{l:56,r:56,t:30,b:36},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',
    font:{family:'IBM Plex Sans, system-ui, sans-serif',size:12,color:css('--ink')},
    xaxis:axis({title:{text:'time [s]'}}),yaxis:axis(),shapes,annotations:ann,
    legend:{orientation:'h',x:1,xanchor:'right',y:1,yanchor:'bottom',bgcolor:'rgba(0,0,0,0)',font:{size:11}},
    hovermode:'x unified',hoverlabel:{bgcolor:css('--panel'),bordercolor:css('--rule'),font:{color:css('--ink')}}},extra||{});
}
const L=(y,name,c,o)=>Object.assign({x:D.t,y,name,type:'scatter',mode:'lines',line:{color:c,width:1.3}},o||{});
const hmax=Math.max(5,...D.alt.filter(v=>v!==null));
let xr=null;
// Mean wind as an arrow in the ground track's top-left corner, pointing downwind (direction is "from").
function windArrow(){
  const S=(W&&W.mean_spd!=null)?W:(H&&H.mean_spd!=null?H:null);
  if(!S)return [];
  const src=S===W?'wind drone':'HWAS station';
  const to=(S.mean_from+180)*Math.PI/180,len=32,col=css('--ink');
  // Sits in the top margin, left of the plot: arrow (centered on a point 20 px in, 22 px up) then the label.
  const cx=20,cy=22;
  return [{xref:'paper',yref:'paper',x:0,y:1,xshift:cx+len/2*Math.sin(to),yshift:cy+len/2*Math.cos(to),
      ax:-len*Math.sin(to),ay:len*Math.cos(to),axref:'pixel',ayref:'pixel',
      showarrow:true,arrowhead:2,arrowsize:1.2,arrowwidth:2.5,arrowcolor:col,text:''},
    {xref:'paper',yref:'paper',x:0,y:1,xshift:44,yshift:cy,xanchor:'left',yanchor:'middle',showarrow:false,
      text:`<b>Mean wind ${S.mean_spd.toFixed(1)} m/s</b> from ${S.mean_from}° (${S.mean_from_name}) · ${src}`,font:{size:12,color:col}}];
}
const PR=(...a)=>Plotly.react(...a);
function draw(){
  const react=(id,...a)=>{if(document.getElementById(id))return PR(id,...a);};
  const c1=css('--c1'),c2=css('--c2'),c3=css('--c3'),c4=css('--c4');
  const tr={x:D.E,y:D.N,type:'scatter',mode:'markers',name:'track',marker:{size:3,color:D.t,colorscale:'Viridis',showscale:true,colorbar:{title:{text:'s',font:{color:css('--muted')}},thickness:10,tickfont:{color:css('--muted')},len:.8}},
    customdata:D.t.map((t,i)=>[t,D.alt[i],D.gs[i]]),hovertemplate:'t %{customdata[0]:.1f} s<br>E %{x:.1f} m, N %{y:.1f} m<br>alt %{customdata[1]:.1f} m, gs %{customdata[2]:.1f} m/s<extra></extra>'};
  const home={x:[0],y:[0],type:'scatter',mode:'markers',name:'home',marker:{symbol:'triangle-up',size:13,color:c4},hovertemplate:'home<extra></extra>'};
  const sel=[];
  if(xr){const i0=D.t.findIndex(t=>t>=xr[0]),i1=D.t.findIndex(t=>t>xr[1]);const j=i1<0?D.t.length:i1;
    if(i0>=0)sel.push({x:D.E.slice(i0,j),y:D.N.slice(i0,j),type:'scatter',mode:'lines',name:'zoomed range',line:{color:c4,width:4},hoverinfo:'skip'});}
  react('p-track',[tr,home,...sel],{margin:{l:52,r:10,t:W?50:10,b:40},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',
    font:{family:'IBM Plex Sans, system-ui, sans-serif',size:12,color:css('--ink')},showlegend:false,
    xaxis:axis({title:{text:'east [m]'}}),yaxis:axis({title:{text:'north [m]'},scaleanchor:'x',scaleratio:1}),
    hoverlabel:{bgcolor:css('--panel'),bordercolor:css('--rule'),font:{color:css('--ink')}},annotations:windArrow()},cfg);
  // 3D: one colored segment per flight-mode run
  const pal=[c1,c2,c3,c4],names=[...new Set(D.mode)],cm={};names.forEach((n,i)=>cm[n]=names.length===1?c1:(n==='—'?css('--muted'):pal[i%4]));
  const mlabel=m=>names.length===1&&m==='—'?'flight path':(m==='—'?'no mode':m);
  const segs=[],seen=new Set();let st=0;
  for(let i=1;i<=D.mode.length;i++){if(i===D.mode.length||D.mode[i]!==D.mode[st]){const a=Math.max(0,st-1),m=D.mode[st];
    segs.push({type:'scatter3d',mode:'lines',name:mlabel(m),legendgroup:m,showlegend:!seen.has(m),x:D.E.slice(a,i),y:D.N.slice(a,i),z:D.alt.slice(a,i),
      customdata:D.t.slice(a,i),line:{color:cm[m],width:6},hovertemplate:'t %{customdata:.1f} s<br>E %{x:.0f} m, N %{y:.0f} m<br>h %{z:.1f} m<extra>'+m+'</extra>'});seen.add(m);st=i;}}
  const shadow={type:'scatter3d',mode:'lines',name:'ground track',x:D.E,y:D.N,z:D.alt.map(()=>0),line:{color:css('--rule'),width:3},hoverinfo:'skip'};
  const dx=[],dy=[],dz=[],every=Math.max(1,Math.round(5/((D.t[1]-D.t[0])||0.1)));
  for(let i=0;i<D.alt.length;i+=every){if(D.alt[i]>1){dx.push(D.E[i],D.E[i],null);dy.push(D.N[i],D.N[i],null);dz.push(0,D.alt[i],null);}}
  const drops={type:'scatter3d',mode:'lines',name:'drop lines (5 s)',x:dx,y:dy,z:dz,line:{color:css('--rule'),width:2},hoverinfo:'skip'};
  const hm={type:'scatter3d',mode:'markers',name:'home',x:[0],y:[0],z:[0],marker:{size:5,color:c4,symbol:'diamond'},hovertemplate:'home<extra></extra>'};
  const sa=a=>Object.assign(axis(a),{backgroundcolor:'rgba(0,0,0,0)',showbackground:false});
  const ex=v=>v.filter(x=>x!==null),span=a=>Math.max(1,Math.max(...ex(a))-Math.min(...ex(a)));
  const sx=span(D.E),sy=span(D.N),big=Math.max(sx,sy);
  react('p-3d',[shadow,drops,...segs,hm],{margin:{l:0,r:0,t:0,b:0},paper_bgcolor:'rgba(0,0,0,0)',font:{family:'IBM Plex Sans, system-ui, sans-serif',size:12,color:css('--ink')},
    legend:{orientation:'h',x:0,y:1,bgcolor:'rgba(0,0,0,0)'},uirevision:'3d',
    scene:{xaxis:sa({title:{text:'east [m]'}}),yaxis:sa({title:{text:'north [m]'}}),zaxis:sa({title:{text:'height [m]'},range:[0,hmax*1.15]}),
      aspectmode:'manual',aspectratio:{x:1.2*sx/big,y:1.2*sy/big,z:0.36},camera:{eye:{x:1.35,y:-1.3,z:0.75},center:{x:0,y:0,z:-0.15}}},
    hoverlabel:{bgcolor:css('--panel'),bordercolor:css('--rule'),font:{color:css('--ink')}}},cfg);
  const X=xr?{range:xr}:{range:[D.t[0],D.t[D.t.length-1]]};
  const B=(ex)=>{const b=base(ex);b.xaxis=Object.assign(b.xaxis,X);return b;};
  // One line; the right axis is the same height above mean sea level (offset by the home elevation).
  const av=D.alt.filter(v=>v!==null),alo=Math.min(0,...av),ahi=Math.max(1,...av),pad=(ahi-alo)*0.06,yr=[alo-pad,ahi+pad];
  const HM=D.home_msl,altTr=[L(D.alt,'above takeoff',c1,{customdata:D.alt.map(v=>v===null||HM===null?null:(v+HM).toFixed(1)),
      hovertemplate:HM===null?'%{y:.1f} m':'%{y:.1f} m above takeoff<br>%{customdata} m MSL<extra></extra>'})];
  if(HM!==null)altTr.push({x:[D.t[0]],y:[yr[0]+HM],yaxis:'y2',type:'scatter',mode:'markers',marker:{opacity:0},showlegend:false,hoverinfo:'skip'});
  react('p-alt',altTr,B({yaxis:axis({title:{text:'m above takeoff'},range:yr}),
    yaxis2:HM===null?undefined:axis({title:{text:'m MSL'},overlaying:'y',side:'right',showgrid:false,range:[yr[0]+HM,yr[1]+HM],tickformat:'.0f'}),showlegend:false}),cfg);
  react('p-gs',[L(D.gs,'groundspeed',c1),L(D.vz,'climb rate',c2)],B({yaxis:axis({title:{text:'m/s'}})}),cfg);
  react('p-att',[L(D.roll,'roll',c1),L(D.pitch,'pitch',c2)],B({yaxis:axis({title:{text:'deg'}})}),cfg);
  react('p-bat',[L(D.V,'voltage',c1),L(D.I,'current',c4,{yaxis:'y2',line:{color:c4,width:1}})],
    B({yaxis:axis({title:{text:'V'}}),yaxis2:axis({title:{text:'A'},overlaying:'y',side:'right',showgrid:false,rangemode:'tozero'})}),cfg);
  react('p-mot',[L(D.m1,'SRV1',c1),L(D.m2,'SRV2',c2),L(D.m3,'SRV3',c3),L(D.m4,'SRV4',c4)],B({yaxis:axis({title:{text:'µs'}})}),cfg);
  react('p-vib',[L(D.vx,'X',c1),L(D.vy,'Y',c2),L(D.vzb,'Z',c3)],B({yaxis:axis({title:{text:'m/s²'},rangemode:'tozero'})}),cfg);
  react('p-gps',[L(D.fixt,'fix type',c1,{line:{color:c1,width:2,shape:'hv'},customdata:D.fixt.map(v=>FIXN[v]||''),hovertemplate:'%{customdata}<extra>fix</extra>'}),
      L(D.sats,'satellites',c2,{yaxis:'y2',line:{color:c2,width:1}})],
    B({yaxis:axis({title:{text:'fix'},range:[2.5,6.5],tickvals:[3,4,5,6],ticktext:['3D','DGPS','RTK Float','RTK Fixed']}),
       yaxis2:axis({title:{text:'sats'},overlaying:'y',side:'right',showgrid:false,rangemode:'tozero'}),margin:{l:80,r:56,t:30,b:36}}),cfg);
  react('p-ekf',[L(D.ev,'velocity',c1),L(D.eph,'pos horiz',c3),L(D.epv,'pos vert',c2),L(D.ec,'compass',c4)],
    B({yaxis:axis({title:{text:'variance'},rangemode:'tozero'}),shapes:base().shapes.concat([{type:'line',xref:'paper',x0:0,x1:1,y0:.8,y1:.8,line:{color:c4,width:1,dash:'dot'}}])}),cfg);
  const wt=[];
  if(W)wt.push({x:W.t,y:W.spd,name:'wind drone',type:'scatter',mode:'lines',line:{color:c1,width:1.3}},
      {x:W.t,y:W.dir,name:'wind drone dir',type:'scatter',mode:'markers',yaxis:'y2',marker:{color:c1,size:3,opacity:.5},showlegend:false});
  if(H)wt.push({x:H.t,y:H.spd,name:'HWAS',type:'scatter',mode:'lines+markers',line:{color:c3,width:2.2,shape:'hv'},marker:{size:5}},
      {x:H.t,y:H.gust,name:'HWAS gust',type:'scatter',mode:'lines',line:{color:c3,width:1.2,dash:'dot',shape:'hv'},connectgaps:false},
      {x:H.t,y:H.dir,name:'HWAS dir',type:'scatter',mode:'markers',yaxis:'y2',marker:{color:c3,size:6,symbol:'diamond'},showlegend:false});
  if(wt.length)react('p-wind',wt,B({yaxis:axis({title:{text:'m/s'},rangemode:'tozero'}),
    yaxis2:axis({title:{text:'from [deg]'},overlaying:'y',side:'right',showgrid:false,range:[0,360],dtick:90})}),cfg);
  const tt=[];
  if(H)tt.push({x:H.t,y:H.temp,name:'air (HWAS)',type:'scatter',mode:'lines+markers',line:{color:c3,width:2.2,shape:'hv'},marker:{size:5}});
  if(W)tt.push({x:W.t,y:W.temp,name:'wind-drone sensor',type:'scatter',mode:'lines',line:{color:c1,width:1.3}});
  if(has('btemp'))tt.push(L(D.btemp,'battery',c4,{line:{color:c4,width:1.6}}));
  if(tt.length)react('p-temp',tt,B({yaxis:axis({title:{text:'°C'}})}),cfg);
}
let busy=false;
function nearest(a,x){let lo=0,hi=a.length-1;while(hi-lo>1){const m=(lo+hi)>>1;if(a[m]<x)lo=m;else hi=m;}return Math.abs(a[lo]-x)<=Math.abs(a[hi]-x)?lo:hi;}
function hook(){
  TS.forEach(id=>{const el=document.getElementById(id);
    el.on('plotly_relayout',ev=>{if(busy)return;
      if(ev['xaxis.range[0]']!==undefined)xr=[ev['xaxis.range[0]'],ev['xaxis.range[1]']];
      else if(ev['xaxis.autorange'])xr=null; else return;
      busy=true;draw();busy=false;});
    el.on('plotly_hover',ev=>{const i=nearest(D.t,ev.points[0].x);
      let s=`t ${D.t[i].toFixed(2)} s | ${D.local[i]} | ${D.mode[i]} | alt ${D.alt[i]} m | gs ${D.gs[i]} m/s | ${D.V[i]} V ${D.I[i]} A | ${D.mah[i]} mAh | ${FIXN[D.fixt[i]]||'GPS'} | sats ${D.sats[i]} hdop ${D.hdop[i]}`;
      if(W&&W.t.length){const j=nearest(W.t,D.t[i]);if(Math.abs(W.t[j]-D.t[i])<1)s+=` | wind ${W.spd[j]} m/s @ ${W.dir[j]}°`;}
      if(H&&H.t.length){const j=nearest(H.t,D.t[i]);if(Math.abs(H.t[j]-D.t[i])<=15)s+=` | HWAS ${H.spd[j]} m/s @ ${H.dir[j]}°, ${H.temp[j]} °C`;}
      document.getElementById('readout').textContent=s;});
  });
}
if(D.home_msl!==null){const c=document.getElementById('alt-cap');if(c)c.textContent=`Left axis: height above takeoff (EKF, barometer-based). Right axis: the same above mean sea level, with the takeoff elevation (${D.home_msl.toFixed(1)} m) from ${D.home_src||'the EKF'}; absolute elevation is good to a few meters.`;}
if(window.Plotly){
  draw();hook();
  document.getElementById('reset').addEventListener('click',()=>{xr=null;busy=true;draw();busy=false;});
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{busy=true;draw();busy=false;});
  new MutationObserver(()=>{busy=true;draw();busy=false;}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
}else{
  document.querySelectorAll('.plot').forEach(el=>{el.style.height='auto';el.textContent='The plotting library did not load. Check your connection and reload.';});
}
</script>
</body>
</html>
"""
