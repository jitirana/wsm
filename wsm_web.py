"""wsm_web.py - read-only web page of the WSM: spots heard (locator = link to Google Maps), band summary and receiver state."""
import csv
import hashlib
import hmac
import io
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import wsm_geo
import wsm_landing
import wsm_wsprnet

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>WSM - WSPR System Monitor</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta name="theme-color" content="#0b6b8a">
<style>
:root{--bg:#f4f1ec;--card:#fffdf9;--ink:#1d2430;--mute:#667085;--line:#e4ded3;--acc:#0b6b8a;--ok:#1f7a3d;--warn:#a15c00;--bad:#b42318;--chip:#efe9dd}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#10151c;--card:#171e28;--ink:#e6eaf0;--mute:#9aa6b6;--line:#263041;--acc:#5ab8d8;--ok:#53c27a;--warn:#e0a03a;--bad:#f0746a;--chip:#222c3a}}
:root[data-theme=dark]{--bg:#10151c;--card:#171e28;--ink:#e6eaf0;--mute:#9aa6b6;--line:#263041;--acc:#5ab8d8;--ok:#53c27a;--warn:#e0a03a;--bad:#f0746a;--chip:#222c3a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:16px}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;margin-bottom:12px}
h1{font-size:22px;margin:0}h1 small{font-weight:400;color:var(--mute);font-size:14px;margin-left:6px}
a{color:var(--acc)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
#wfBox{display:grid;grid-template-columns:34px 1fr;grid-template-rows:auto auto;gap:0 6px}
#wfTime{grid-row:1;grid-column:1;height:340px;position:relative;font-size:12px;color:var(--mute)}#wfTime span{position:absolute;right:0;transform:translateY(-50%)}
#wfImgW{grid-row:1;grid-column:2;position:relative;border-radius:8px;overflow:hidden;background:#080c28}
#wfImg{display:block;width:100%;height:340px;image-rendering:auto}
#wfLabels span{position:absolute;transform:translateX(-50%);white-space:nowrap;font:600 12px/1.2 system-ui;color:#fff;background:rgba(0,0,0,.55);
 border:1px solid rgba(255,255,255,.5);border-radius:6px;padding:1px 5px;pointer-events:none}
#wfLabels span.own{border-style:dashed;opacity:.8}
#wfAxis{grid-row:2;grid-column:2;position:relative;height:30px;font-size:12px;color:var(--mute)}#wfAxis span{position:absolute;top:3px;white-space:nowrap;transform:translateX(-50%)}
#wfTabs button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.hq{display:inline-flex;align-items:center;justify-content:center;width:17px;height:17px;padding:0;margin-left:5px;border-radius:50%;
 border:1px solid var(--line);background:var(--chip);color:var(--mute);font:700 11px/1 system-ui;vertical-align:1px;cursor:help;text-transform:none}
.card .k .hq{float:right;margin:0 0 0 6px}
.hq:hover,.hq.on{border-color:var(--acc);color:var(--acc)}
#tip{position:fixed;z-index:2000;max-width:min(330px,calc(100vw - 24px));background:var(--card);color:var(--ink);border:1px solid var(--acc);
 border-radius:10px;padding:9px 12px;font:13.5px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25);
 text-transform:none;letter-spacing:0;white-space:normal;text-align:left}
#tip b{color:var(--acc)}
.panel h2 .ht{display:inline-flex;align-items:center}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:12px}
.chart{position:relative;min-width:0}.chart h3{font-size:13px;font-weight:600;margin:0 0 2px;display:flex;justify-content:space-between;gap:8px}
.chart h3 span{font-weight:400;color:var(--mute)}
.chart svg{display:block;width:100%;height:150px;overflow:visible}
.chart .ax{fill:var(--mute);font-size:10px}.chart .gl{stroke:var(--line);stroke-width:1}
.chart .xh{stroke:var(--mute);stroke-width:1;stroke-dasharray:2 3}
.chart .tt{position:absolute;top:22px;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:3px 7px;font-size:12px;
 white-space:nowrap;box-shadow:0 4px 12px rgba(0,0,0,.15);display:none;z-index:5}
.chk{display:inline-flex;align-items:center;gap:5px;color:var(--mute);font-size:14px;cursor:pointer;user-select:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.card .k{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.card .v{font-size:24px;font-weight:650;margin-top:2px}.card .s{color:var(--mute);font-size:13px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:14px}
.panel h2{font-size:16px;margin:0 0 8px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.panel h2 span{font-weight:400;color:var(--mute);font-size:13px}
.tools{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 10px}
select,input,button{font:inherit;color:var(--ink);background:var(--chip);border:1px solid var(--line);border-radius:8px;padding:5px 9px}
button{cursor:pointer}button.on{border-color:var(--acc)}
.tw{overflow-x:auto}
.og{display:grid;grid-template-columns:44px repeat(24,minmax(11px,1fr));gap:2px;font-size:11px;align-items:center;min-width:480px}
.og .bl{color:var(--ink);font-weight:600;grid-row:span 2}.og .bl.one{grid-row:auto}.og .hd{color:var(--mute);text-align:center;font-size:10px}
.og .c{height:13px;border-radius:2px;background:var(--chip)}.og .c.nd{background:repeating-linear-gradient(45deg,transparent 0 3px,var(--line) 3px 4px)}
.og .c.cur{outline:2px solid var(--ink);outline-offset:-1px}
.og .m{background:var(--acc)}.og .r{background:var(--mute)}.og .c.z{background:var(--chip);border:1px dashed var(--line)}
.lg{display:flex;flex-wrap:wrap;gap:12px;color:var(--mute);font-size:12px;margin:8px 0 0}.lg i{display:inline-block;width:12px;height:10px;border-radius:2px;vertical-align:-1px;margin-right:4px}
table{border-collapse:collapse;width:100%;min-width:720px}
th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mute);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
th:first-child,td:first-child,th.l,td.l{text-align:left}
tr:hover td{background:color-mix(in srgb,var(--chip) 60%,transparent)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--chip);font-size:12px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.mute{color:var(--mute)}
body.pub #csv{display:none}body.pub #pubTag{display:inline-block}
#pubTag{display:none;font-size:12px;padding:1px 8px;border-radius:999px;border:1px solid var(--line);color:var(--mute)}
.note{color:var(--mute);font-size:13px;margin-top:8px}
.cx{border:0;background:none;color:var(--mute);padding:0 4px;font-size:13px}
.card.min>:not(h2){display:none}
#map{height:min(62vh,500px);min-height:300px;border-radius:8px;border:1px solid var(--line);background:var(--chip)}
.leaflet-container{font:inherit}.leaflet-popup-content-wrapper{color:#1d2430}.leaflet-popup-content{margin:10px 12px;line-height:1.4}
#legend{display:inline-flex;flex-wrap:wrap;gap:4px 10px;align-items:center;color:var(--mute);font-size:13px}
#legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:-1px}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(.92) contrast(.9)}}
:root[data-theme=dark] .leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(.92) contrast(.9)}
</style>
<link rel="stylesheet" href="static/leaflet.css">
</head><body><main>
<header><h1>WSM <small>WSPR System Monitor</small></h1><span id="who" class="mute"></span><span id="pubTag" title="you are seeing the public view of this station">public view</span>
<span style="flex:1"></span><a href="/about" style="color:var(--acc);text-decoration:none;font-weight:600;margin-right:12px">What is WSPR?</a><span id="upd" class="mute"></span></header>
<div class="cards" id="cards"></div>
<div class="panel" id="wfP" style="display:none"><h2><b class="ht">Last slot · waterfall<button class="hq" data-h="wf" aria-label="explain">?</button></b><span id="wfInfo"></span></h2>
<div class="tools" id="wfTabs"></div>
<div id="wfBox"><div id="wfTime"></div><div id="wfImgW"><img id="wfImg" alt="waterfall of the last slot"><div id="wfLabels"></div></div><div id="wfAxis"></div></div>
<p class="note">What the receiver heard in the last 2-minute slot of the band: frequency across (the 200 Hz WSPR window), time downwards.
Each WSPR station is a thin vertical trace about 110 s long; the decoded ones are labelled with their callsign and SNR. Brighter = stronger.</p></div>
<div class="panel" id="bandsP"><h2><b class="ht">Bands<button class="hq" data-h="bands" aria-label="explain">?</button></b><span id="hrsB">last 24 h</span></h2><div class="tw"><table id="bands"></table></div></div>
<div class="panel" id="openP"><h2><b class="ht">Band openings<button class="hq" data-h="open" aria-label="explain">?</button></b><span id="openInfo"></span></h2>
<div class="tools"><button data-d="7">7 days</button><button data-d="30" class="on">30 days</button><button data-d="90">90 days</button><button data-d="0">All</button>
<button id="openTz">UTC</button><button id="openRef" class="on">WSPRnet reference</button></div>
<div class="tw"><div class="og" id="openG"></div></div>
<div class="lg"><span><i style="background:var(--acc)"></i>heard by this receiver (spots per listened hour)</span><span><i style="background:var(--mute)"></i>WSPRnet reference (spots per day)</span><span><i style="background:repeating-linear-gradient(45deg,transparent 0 3px,var(--line) 3px 4px)"></i>not listened</span></div>
<p class="note" id="openNote"></p></div>
<div class="panel" id="mapP"><h2><b class="ht">Map<button class="hq" data-h="map" aria-label="explain">?</button></b><span id="mapInfo"></span></h2>
<div class="tools"><button id="mLines" class="on" title="great-circle lines from the station">lines</button><button id="mRings" title="distance rings">rings</button><button id="mNight" class="on" title="shade the part of the Earth where it is night now">night</button><button id="mFit" title="zoom to all spots">fit</button><span id="legend"></span></div>
<div id="map"></div>
<p class="note">Shows the spots chosen below (period, band, callsign). One dot per station and band, placed at the centre of its 4-character locator square (about +-100 km),
coloured by band; tap a dot for the details. Shaded = night now, the sun symbol = where the Sun is overhead (updated every minute). Map data &copy; OpenStreetMap contributors.</p></div>
<div class="panel" id="spotsP"><h2><b class="ht">Spots heard<button class="hq" data-h="spots_p" aria-label="explain">?</button></b><span id="cnt"></span></h2>
<div class="tools">
<select id="per"><option value="1">last 1 h</option><option value="6">last 6 h</option><option value="24" selected>last 24 h</option><option value="168">last 7 days</option><option value="0">all</option></select>
<select id="bandSel"><option value="">all bands</option></select>
<input id="callQ" placeholder="callsign (e.g. PY*)" size="14">
<label class="chk" title="leave your own transmitter out of the list, map, counts and band table"><input type="checkbox" id="noOwn"> hide my TX</label>
<button id="csv" title="download the spots shown as CSV">CSV</button><button id="th" title="light/dark">theme</button>
</div>
<div class="tw"><table id="spots"></table></div>
<p class="note">Times are UTC (the title shows your local time). SNR is in dB for a 2500 Hz bandwidth. DT: seconds late (+) or early (-) against the nominal start,
1 s after the even minute. The locator link opens Google Maps at the centre of the locator square (WSPR sends 4 characters: about +-100 km).
Distance and bearing are from the station locator.</p></div>
<div class="panel" id="histP" style="display:none"><h2><b class="ht">Health history<button class="hq" data-h="hist" aria-label="explain">?</button></b><span id="histInfo"></span></h2>
<div class="tools"><button data-h="6">6 h</button><button data-h="24" class="on">24 h</button><button data-h="168">7 days</button><button data-h="720">30 days</button></div>
<div class="charts"><div class="chart" id="cTemp"></div><div class="chart" id="cClk"></div><div class="chart" id="cCov"></div><div class="chart" id="cLvl"></div></div>
<p class="note">Computer and receiver over time; move the pointer (or touch) over a chart to read the values. Only visible with the key.</p></div>
</main>
<div id="tip" role="tooltip" hidden></div>
<script src="static/leaflet.js"></script>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// ---- "?" explanations: tap or click a "?" next to a title ----
const HELP={
 open:"When each band is <b>open</b>, by hour of the day. Each band has two rows. <b>Top row</b>: what this receiver decoded, spots per hour of listening (darker = more); hatched = it never listened at that hour (the schedule may skip the band). <b>Bottom row</b>: the public WSPRnet database for stations around you, spots of at least a few hundred km per day, shown while your own data is thin. Hover a cell for numbers: spots, stations, best DX, SNR. The outlined column is the current hour. Times are UTC unless you switch to local.",
 radio:"State of the receiver. <b>listening</b> = recording a 2-minute slot; <b>opening</b> = starting the radio; <b>error</b> = see the log. Below: band of this slot, antenna input and time to the next slot. WSM listens to one band per slot, following the rotation (or the day/night schedule) in wsm.yaml.",
 level:"Strongest sample of the last slot, as a share of the radio's full scale. Keep it <b>below about 50 %</b>; above 90 % the receiver overloads and invents false signals, so lower <b>gain_db</b>. <b>mean</b> is the average power in dB (relative): useful to compare the noise of bands and antennas.",
 system:"Health of the computer: CPU temperature, CPU clock (a Raspberry Pi slows down when hot or under-powered), power supply and <b>coverage</b>, the share of the samples actually recorded in the slots of the last hour. Below 100 % the computer is losing samples and weak stations are missed. Hover the card for details.",
 spots:"A <b>spot</b> is one decoded transmission: one station heard in one 2-minute slot. A station heard in 10 slots gives 10 spots. The period is the one chosen above the spot list.",
 stations:"How many <b>different callsigns</b> were decoded in the period.",
 bestdx:"The farthest station heard in the period: distance from this station to the centre of the other station's locator square (about +-100 km). Your own transmitter never counts.",
 bestall:"The farthest station this receiver has ever heard, with band, date and SNR.",
 wf:"A picture of the last 2-minute slot of a band, from the same samples the decoder used. Frequency goes across (+-150 Hz around the centre of the WSPR window), time downwards. Each WSPR transmission is a thin, slightly wavy vertical line about 110 s long, starting 1 s after the even minute. Decoded ones are labelled. A line from top to bottom (0 to 120 s), perfectly straight, is not WSPR but a steady carrier or interference.",
 bands:"For each band in the period: <b>Slots</b> = 2-minute recordings made, <b>Spots</b> = transmissions decoded, <b>Stations</b> = different callsigns, <b>Last slot</b> = when the band was last listened to. Many slots and no spots = band closed at that time, or the antenna is weak there.",
 hist:"How the computer and the receiver behaved over time. <b>CPU temperature</b>: the dashed line is the alert level. <b>CPU clock</b>: the lowest clock in each interval; drops to 600 MHz mean under-voltage or overheating (red marks = under-voltage seen). <b>Slot coverage</b>: share of the samples recorded in each slot; below 100 % the computer lost samples. <b>Noise level</b>: mean input power of each slot per band, in dB below full scale; it rises with local interference, falls when a band closes. Gaps = WSM was not running.",
 map:"Every station of the spot list below, placed at the centre of its locator square and coloured by band. Lines are great-circle paths, the shortest way over the globe, roughly the way the signal travelled. Tap a dot for details. The <b>shaded part is night</b> right now: low bands (80, 40, 30 m) carry far when the path is in darkness, high bands (15, 10 m) need daylight. The edge of the shade is the <b>grey line</b> (sunrise / sunset), often good for long distance.",
 spots_p:"Every transmission decoded, newest first. Filter by period, band or callsign (* is a wildcard: PY*). <b>hide my TX</b> leaves your own beacon out. CSV downloads what is shown.",
 time:"Start of the 2-minute slot, in UTC (the time used by all radio amateurs). Hover a time to see your local time.",
 band:"Amateur band, named by its wavelength: 40m = 7 MHz, 30m = 10 MHz, 20m = 14 MHz, 10m = 28 MHz. Lower bands carry farther at night, higher bands by day.",
 freq:"Exact frequency of the received signal, MHz. All WSPR stations of a band share a 200 Hz window (dial + 1400 to 1600 Hz) and pick different spots in it.",
 snr:"Signal-to-noise ratio in dB, in a 2500 Hz bandwidth (the WSJT-X convention). <b>Negative is normal</b>: WSPR decodes down to about -28 dB, far below what the ear can hear in the noise. -10 dB is a strong signal.",
 dt:"Time offset in seconds: how late (+) or early (-) the transmission started against the nominal start, 1 s after the even minute. Within +-1 s is normal. A large DT on every spot means this receiver's clock (or clock_offset_s) is off; on one station only, that station's clock is wrong.",
 drift:"How much the transmitter frequency changed during the 2 minutes, Hz per minute. 0 is ideal; 1 to 4 usually means a transmitter warming up or an oscillator without temperature control.",
 call:"Callsign of the transmitting station. In angle brackets (&lt;PY2ABC&gt;) = a long or compound callsign, sent as a short code and resolved from an earlier message.",
 loc:"Maidenhead locator sent by the station: 4 characters = a square of 1 x 2 degrees, about 110 x 220 km. The link opens the centre of the square on Google Maps.",
 power:"Power the transmitter says it uses, in dBm: 10 dBm = 10 mW, 23 = 200 mW, 30 = 1 W, 37 = 5 W. A few hundred milliwatts heard thousands of km away is common with WSPR.",
 dist:"Great-circle distance from this station, km. From locator centres, so about +-100 km.",
 brg:"Bearing: the direction from this station towards the transmitter, degrees from true north (0 = N, 90 = E, 180 = S, 270 = W)."};
const hq=k=>`<button class="hq" data-h="${k}" aria-label="explain">?</button>`;
let tipBtn=null;
function tipClose(){$("tip").hidden=true;if(tipBtn)tipBtn.classList.remove("on");tipBtn=null}
document.addEventListener("click",e=>{
 const b=e.target.closest(".hq");
 if(!b){if(!e.target.closest("#tip"))tipClose();return}
 e.preventDefault();e.stopPropagation();
 if(tipBtn&&tipBtn.dataset.h===b.dataset.h&&!$("tip").hidden){tipClose();return}
 tipClose();const t=$("tip");t.innerHTML=HELP[b.dataset.h]||"";t.hidden=false;tipBtn=b;b.classList.add("on");
 const r=b.getBoundingClientRect(),w=t.offsetWidth,h=t.offsetHeight;
 t.style.left=Math.max(12,Math.min(innerWidth-w-12,r.left+r.width/2-w/2))+"px";
 t.style.top=(r.bottom+8+h<innerHeight?r.bottom+8:Math.max(8,r.top-8-h))+"px"});
document.addEventListener("keydown",e=>{if(e.key==="Escape")tipClose()});
addEventListener("scroll",tipClose,{passive:true});addEventListener("resize",tipClose);
const fmtT=t=>{const d=new Date(t*1000);return d.toISOString().slice(0,16).replace("T"," ")};
const ago=t=>{const s=Math.max(0,Math.round(Date.now()/1000-t));return s<90?s+" s":s<5400?Math.round(s/60)+" min":(s/3600).toFixed(1)+" h"};
function q(){const h=+$("per").value,b=$("bandSel").value,c=$("callQ").value.trim();
 return "since="+(h?Math.floor(Date.now()/1000-h*3600):0)+(b?"&band="+encodeURIComponent(b):"")+(c?"&call="+encodeURIComponent(c):"")+($("noOwn").checked?"&noown=1":"")}
// ---- map (Leaflet) ----
let MAP=null,LAYER=null,mapKey=null,showLines=true,showRings=false,lastSp=null,lastSt=null;
const BANDS_ORDER=["160m","80m","60m","40m","30m","20m","17m","15m","12m","10m","6m","4m","2m","70cm"];
const bandColor=b=>{const i=BANDS_ORDER.indexOf(b);return "hsl("+(((i<0?0:i)*47)%360)+",70%,42%)"};
function unwrap(pts){let prev=pts[0][1];for(const p of pts){while(p[1]-prev>180)p[1]-=360;while(p[1]-prev<-180)p[1]+=360;prev=p[1]}return pts}
function gc(a,b,n){ // great-circle path from a to b, longitudes unwrapped so the line never jumps across the date line
 const r=Math.PI/180,la1=a[0]*r,lo1=a[1]*r,la2=b[0]*r,lo2=b[1]*r;
 const d=2*Math.asin(Math.min(1,Math.sqrt(Math.sin((la2-la1)/2)**2+Math.cos(la1)*Math.cos(la2)*Math.sin((lo2-lo1)/2)**2)));
 if(d<1e-9||Math.sin(d)<1e-6)return [a.slice(),b.slice()];
 const out=[];
 for(let i=0;i<=n;i++){const f=i/n,A=Math.sin((1-f)*d)/Math.sin(d),B=Math.sin(f*d)/Math.sin(d);
  const x=A*Math.cos(la1)*Math.cos(lo1)+B*Math.cos(la2)*Math.cos(lo2),y=A*Math.cos(la1)*Math.sin(lo1)+B*Math.cos(la2)*Math.sin(lo2),z=A*Math.sin(la1)+B*Math.sin(la2);
  out.push([Math.atan2(z,Math.sqrt(x*x+y*y))/r,Math.atan2(y,x)/r])}
 return unwrap(out)}
function ring(c,km){ // circle of radius km around c
 const r=Math.PI/180,d=km/6371,la=c[0]*r,lo=c[1]*r,pts=[];
 for(let b=0;b<=360;b+=4){const t=b*r,la2=Math.asin(Math.sin(la)*Math.cos(d)+Math.cos(la)*Math.sin(d)*Math.cos(t));
  const lo2=lo+Math.atan2(Math.sin(t)*Math.sin(d)*Math.cos(la),Math.cos(d)-Math.sin(la)*Math.sin(la2));pts.push([la2/r,lo2/r])}
 return unwrap(pts)}
function drawMap(spots,st){
 lastSp=spots;lastSt=st;
 if(typeof L==="undefined"){$("mapInfo").textContent="map library not loaded";return}
 const s=st.station;let me=null;
 if(s&&s.lat!=null&&s.lon!=null)me=[+s.lat,+s.lon];
 else{const m=/q=(-?[\d.]+),(-?[\d.]+)/.exec(st.map||"");if(m)me=[+m[1],+m[2]]}
 if(!MAP){
  MAP=L.map("map",{worldCopyJump:true,minZoom:1}).setView(me||[0,0],4);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:12,referrerPolicy:"origin",
   attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'}).addTo(MAP);
  LAYER=L.layerGroup().addTo(MAP);drawNight()}
 LAYER.clearLayers();
 const agg=new Map();
 for(const r of spots){if(r.lat==null)continue;const k=r.call+"|"+r.band+"|"+r.grid;let a=agg.get(k);
  if(!a){a=Object.assign({},r,{n:0,best:r.snr});agg.set(k,a)}a.n++;if(r.snr>a.best)a.best=r.snr;if(r.ts>a.ts)a.ts=r.ts}
 const bounds=[];
 if(me){bounds.push(me);
  L.circleMarker(me,{radius:9,color:getComputedStyle(document.documentElement).getPropertyValue("--acc")||"#0b6b8a",weight:3,fillColor:"#fff",fillOpacity:1})
   .bindTooltip(esc(s.callsign)+" (station)").addTo(LAYER);
  if(showRings)for(const km of [1000,2500,5000,10000,15000])
   L.polyline(ring(me,km),{color:"#888",weight:1,dashArray:"4 6",interactive:true}).bindTooltip(km+" km",{sticky:true}).addTo(LAYER)}
 const bands=new Set();
 for(const a of agg.values()){
  const col=bandColor(a.band),path=me?gc(me,[a.lat,a.lon],40):[[a.lat,a.lon]],end=path[path.length-1];
  bands.add(a.band);bounds.push(end);
  if(showLines&&me)L.polyline(path,{color:col,weight:1.5,opacity:.55,interactive:false}).addTo(LAYER);
  const pop=`<b>${esc(a.call)}</b> · ${esc(a.band)}<br>locator <a href="${esc(a.map)}" target="_blank" rel="noopener">${esc(a.grid)}</a>`
   +`<br>${a.dist_km==null?"":Math.round(a.dist_km)+" km · "+Math.round(a.bearing)+"°<br>"}`
   +`best SNR ${a.best} dB · ${a.n} spot${a.n>1?"s":""}<br>last heard ${fmtT(a.ts)} UTC`;
  L.circleMarker(end,{radius:6,color:"#fff",weight:1.5,fillColor:col,fillOpacity:.95}).bindPopup(pop).addTo(LAYER)}
 $("legend").innerHTML=BANDS_ORDER.filter(b=>bands.has(b)).concat([...bands].filter(b=>BANDS_ORDER.indexOf(b)<0))
  .map(b=>`<span><i style="background:${bandColor(b)}"></i>${esc(b)}</span>`).join("");
 $("mapInfo").textContent=agg.size+" station"+(agg.size===1?"":"s")+" · "+bands.size+" band"+(bands.size===1?"":"s");
 const key=$("per").value+"|"+$("bandSel").value+"|"+$("callQ").value.trim();
 if(key!==mapKey){mapKey=key;                                   // re-centre only when the filters change, never on the 20 s refresh
  if(bounds.length>1)MAP.fitBounds(bounds,{padding:[30,30],maxZoom:8});else if(bounds.length)MAP.setView(bounds[0],6)}
}
$("mLines").addEventListener("click",e=>{showLines=!showLines;e.target.classList.toggle("on",showLines);if(lastSp)drawMap(lastSp,lastSt)});
$("mRings").addEventListener("click",e=>{showRings=!showRings;e.target.classList.toggle("on",showRings);if(lastSp)drawMap(lastSp,lastSt)});
$("mFit").addEventListener("click",()=>{mapKey=null;if(lastSp)drawMap(lastSp,lastSt)});
// ---- day / night: the shade where the Sun is below the horizon now ----
let NIGHT=null,showNight=true;
function sunPos(d){                       // subsolar point (lat, lon) in degrees, low-precision almanac (~0.1 deg)
 const r=Math.PI/180,n=d.getTime()/86400000-10957.5;
 const L=280.460+0.9856474*n,g=(357.528+0.9856003*n)*r,lam=(L+1.915*Math.sin(g)+0.020*Math.sin(2*g))*r,eps=(23.439-4e-7*n)*r;
 const dec=Math.asin(Math.sin(eps)*Math.sin(lam)),ra=Math.atan2(Math.cos(eps)*Math.sin(lam),Math.cos(lam));
 const gmst=(18.697374558+24.06570982441908*n)%24;
 let lon=ra/r-gmst*15;lon=((lon+540)%360)-180;return [dec/r,lon]}
function drawNight(){
 if(!MAP)return;
 if(!NIGHT)NIGHT=L.layerGroup().addTo(MAP);
 NIGHT.clearLayers();if(!showNight)return;
 const r=Math.PI/180,[sl,sn]=sunPos(new Date()),dec=(Math.abs(sl)<0.01?0.01:sl)*r,line=[];
 for(let lon=-540;lon<=540;lon+=1)line.push([Math.atan(-Math.cos((lon-sn)*r)/Math.tan(dec))/r,lon]);
 const pole=dec>0?-85:85;                // the pole away from the Sun is in the night
 const poly=[[pole,-540]].concat(line,[[pole,540]]);
 L.polygon(poly,{stroke:false,fillColor:"#000",fillOpacity:.28,interactive:false}).addTo(NIGHT);
 L.polyline(line,{color:"#f0a030",weight:1,opacity:.7,interactive:false}).addTo(NIGHT);
 for(const k of [-360,0,360])L.marker([sl,sn+k],{icon:L.divIcon({className:"",html:'<div style="font-size:20px;line-height:20px;color:#f5b400;text-shadow:0 0 3px #000">&#9728;</div>',iconSize:[20,20],iconAnchor:[10,10]}),interactive:true})
  .bindTooltip("Sun overhead now").addTo(NIGHT)}
setInterval(drawNight,60000);
$("mNight").addEventListener("click",e=>{showNight=!showNight;e.target.classList.toggle("on",showNight);drawNight()});
// ---- health history graphs ----
let histH=24,histData=null,histT=0;
function fmtLocal(t,long){const d=new Date(t*1000);return long?d.toLocaleString([],{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}):d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}
function chart(id,title,unit,series,o){
 // series: [{pts:[[t,v]...], color, label}]  o: {t0,t1,ymin,ymax,lines:[{y,label}],marks:[t],step,fmt}
 const el=$(id),W=Math.max(260,el.clientWidth||400),H=150,L=40,R=8,T=8,B=20,iw=W-L-R,ih=H-T-B;
 let lo=o.ymin,hi=o.ymax;const all=series.flatMap(s=>s.pts.map(p=>p[1])).filter(v=>v!=null);
 if(lo==null)lo=all.length?Math.min(...all):0;if(hi==null)hi=all.length?Math.max(...all):1;
 for(const l of o.lines||[]){hi=Math.max(hi,l.y);lo=Math.min(lo,l.y)}
 if(hi-lo<1e-9){hi+=1;lo-=1}const pad=(hi-lo)*.08;if(o.ymin==null)lo-=pad;if(o.ymax==null)hi+=pad;
 const X=t=>L+(t-o.t0)/(o.t1-o.t0)*iw,Y=v=>T+(1-(v-lo)/(hi-lo))*ih;
 let g="";const ny=4;
 for(let i=0;i<=ny;i++){const v=lo+(hi-lo)*i/ny,y=Y(v);g+=`<line class="gl" x1="${L}" x2="${W-R}" y1="${y}" y2="${y}"/><text class="ax" x="${L-4}" y="${y+3}" text-anchor="end">${(o.fmt||(x=>Math.round(x)))(v)}</text>`}
 const span=o.t1-o.t0,stepX=span<=6*3600?3600:span<=48*3600?6*3600:span<=8*86400?86400:5*86400;
 const tz=new Date().getTimezoneOffset()*60;
 for(let t=Math.ceil((o.t0-tz)/stepX)*stepX+tz;t<=o.t1;t+=stepX){const x=X(t);g+=`<line class="gl" x1="${x}" x2="${x}" y1="${T}" y2="${T+ih}"/><text class="ax" x="${x}" y="${H-5}" text-anchor="middle">${stepX>=86400?new Date(t*1000).toLocaleDateString([],{day:"2-digit",month:"2-digit"}):fmtLocal(t)}</text>`}
 for(const t of o.marks||[])g+=`<rect x="${X(t)-1}" y="${T}" width="${Math.max(2,o.step/(o.t1-o.t0)*iw)}" height="${ih}" fill="#d92d20" opacity=".35"/>`;
 for(const l of o.lines||[])g+=`<line x1="${L}" x2="${W-R}" y1="${Y(l.y)}" y2="${Y(l.y)}" stroke="#d92d20" stroke-dasharray="5 4" stroke-width="1.2"/><text class="ax" x="${W-R}" y="${Y(l.y)-3}" text-anchor="end" fill="#d92d20">${esc(l.label)}</text>`;
 for(const s of series){let d="",prev=null;
  for(const [t,v] of s.pts){if(v==null){prev=null;continue}const x=X(t).toFixed(1),y=Y(Math.max(lo,Math.min(hi,v))).toFixed(1);
   d+=(prev==null||t-prev>o.step*2.5?"M":"L")+x+" "+y;prev=t}
  g+=`<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.6" stroke-linejoin="round"/>`;
  if(s.pts.length<400)for(const [t,v] of s.pts)if(v!=null)g+=`<circle cx="${X(t).toFixed(1)}" cy="${Y(Math.max(lo,Math.min(hi,v))).toFixed(1)}" r="1.6" fill="${s.color}"/>`}
 const legend=series.length>1?series.map(s=>`<span style="color:${s.color}">● ${esc(s.label)}</span>`).join(" "):"";
 el.innerHTML=`<h3>${esc(title)}<span>${legend}</span></h3><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">${g}<line class="xh" y1="${T}" y2="${T+ih}" x1="0" x2="0" visibility="hidden"/></svg><div class="tt"></div>`;
 const svg=el.querySelector("svg"),xh=el.querySelector(".xh"),tt=el.querySelector(".tt");
 const move=e=>{const r=svg.getBoundingClientRect(),px=(e.clientX-r.left)/r.width*W;if(px<L||px>W-R){tt.style.display="none";xh.setAttribute("visibility","hidden");return}
  const t=o.t0+(px-L)/iw*(o.t1-o.t0);let rows=[],best=null;
  for(const s of series){let nb=null;for(const p of s.pts)if(p[1]!=null&&(nb==null||Math.abs(p[0]-t)<Math.abs(nb[0]-t)))nb=p;
   if(nb&&Math.abs(nb[0]-t)<=o.step*1.5){rows.push(`<span style="color:${s.color}">●</span> ${series.length>1?esc(s.label)+" ":""}<b>${(o.fmt||(x=>x))(nb[1])}</b> ${unit}`);best=best||nb}}
  if(!rows.length){tt.style.display="none";return}
  const x=X(best[0]);xh.setAttribute("x1",x);xh.setAttribute("x2",x);xh.setAttribute("visibility","visible");
  tt.innerHTML=fmtLocal(best[0],true)+"<br>"+rows.join("<br>");tt.style.display="block";
  const left=x/W*r.width;tt.style.left=Math.max(0,Math.min(r.width-tt.offsetWidth,left+10))+"px"};
 svg.addEventListener("pointermove",move);svg.addEventListener("pointerdown",move);
 svg.addEventListener("pointerleave",()=>{tt.style.display="none";xh.setAttribute("visibility","hidden")})}
function drawHist(){
 const d=histData;if(!d)return;
 const t1=Date.now()/1000,t0=t1-d.hours*3600,st=d.bucket;
 const sy=d.sys||[],sl=d.slots||[];
 if(!sy.length&&!sl.length){$("histP").style.display="";for(const id of ["cTemp","cClk","cCov","cLvl"])$(id).innerHTML="";$("cTemp").innerHTML='<p class="mute">No history yet: the first point is written 5 minutes after WSM starts.</p>';return}
 $("histP").style.display="";
 const temps=sy.map(r=>r.temp_c).filter(v=>v!=null);
 $("histInfo").textContent=temps.length?`temperature ${Math.min(...temps).toFixed(0)}–${Math.max(...sy.map(r=>r.temp_max??r.temp_c)).toFixed(0)} °C`:"";
 const wait=t=>`<h3>${t}</h3><p class="mute" style="height:150px;margin:0;padding-top:50px;text-align:center">collecting data: the first point appears 5 minutes after WSM starts</p>`;
 if(!sy.length){$("cTemp").innerHTML=wait("CPU temperature");$("cClk").innerHTML=wait("CPU clock (lowest)")}else{
 chart("cTemp","CPU temperature","°C",[{pts:sy.map(r=>[r.ts,r.temp_c]),color:"#e0702a",label:"mean"},{pts:sy.map(r=>[r.ts,r.temp_max]),color:"#b42318",label:"max"}],
  {t0,t1,step:st,lines:[{y:d.temp_max_c,label:"alert "+d.temp_max_c+" °C"}],fmt:v=>v.toFixed(0)});
 chart("cClk","CPU clock (lowest)","MHz",[{pts:sy.map(r=>[r.ts,r.clock_min]),color:"#2e7dd1",label:"lowest"}],
  {t0,t1,step:st,ymin:0,ymax:Math.ceil(Math.max(1000,...sy.map(r=>r.clock_min||0))/250)*250,marks:sy.filter(r=>r.uv_pct>0).map(r=>r.ts),fmt:v=>Math.round(v)});}
 const slStep=st<=300?120:st;
 chart("cCov","Slot coverage","%",[{pts:sl.map(r=>[r.ts,r.cov]),color:"#1f7a3d",label:"coverage"}],
  {t0,t1,step:Math.max(slStep,600),ymin:Math.min(80,...sl.map(r=>r.cov)),ymax:100,lines:[{y:d.coverage_min,label:"alert "+d.coverage_min+" %"}],fmt:v=>v.toFixed(0)});
 const bands=[...new Set(sl.map(r=>r.band))].sort((a,b)=>BANDS_ORDER.indexOf(a)-BANDS_ORDER.indexOf(b));
 chart("cLvl","Noise level per band","dB",bands.map(b=>({pts:sl.filter(r=>r.band===b).map(r=>[r.ts,r.rms_db]),color:bandColor(b),label:b})),
  {t0,t1,step:Math.max(slStep*6,1800),fmt:v=>v.toFixed(0)})}
async function loadHist(force){
 if(!document.body.classList.contains("auth"))return;
 if(!force&&Date.now()-histT<60000){return}
 histT=Date.now();
 try{histData=await fetch("api/history?hours="+histH).then(r=>{if(!r.ok)throw 0;return r.json()})}catch(e){$("histP").style.display="none";return}
 drawHist()}
for(const b of document.querySelectorAll("#histP .tools button"))b.addEventListener("click",()=>{histH=+b.dataset.h;
 for(const x of document.querySelectorAll("#histP .tools button"))x.classList.toggle("on",x===b);loadHist(true)});
let rsz;addEventListener("resize",()=>{clearTimeout(rsz);rsz=setTimeout(drawHist,200)});
// ---- band openings: band x hour of the day ----
let opD=30,opLocal=false,opRef=true,opData=null,opT=0;
async function loadOpen(force){
 if(!force&&Date.now()-opT<120000)return;opT=Date.now();
 try{opData=await fetch("api/openings?days="+opD).then(r=>{if(!r.ok)throw 0;return r.json()})}catch(e){$("openP").style.display="none";return}
 $("openP").style.display="";drawOpen()}
function drawOpen(){
 const d=opData;if(!d)return;
 const off=opLocal?Math.round(-new Date().getTimezoneOffset()/60):0,hr=i=>((i-off)%24+24)%24;   // column i shows UTC hour hr(i)
 let h='<span></span>';for(let i=0;i<24;i++)h+=`<span class="hd">${i}</span>`;
 const showRef=opRef&&d.reference;
 let anyMine=0,anyRef=0;
 for(const b of d.bands){
  const m=d.mine[b]||[],r=d.ref[b]||[];
  const rate=c=>c&&c.listen_h>0?c.spots/c.listen_h:0,top=Math.max(0.01,...m.map(rate)),rtop=Math.max(0.01,...r.map(c=>c?c.spots_per_day:0));
  h+=`<span class="bl${showRef?"":" one"}">${esc(b)}</span>`;
  for(let i=0;i<24;i++){const u=hr(i),c=m[u],cur=u===d.now_hour?" cur":"";
   if(!c){h+=`<span class="c nd${cur}" title="${esc(b)} ${u}:00 UTC - not listened"></span>`;continue}
   const v=rate(c);anyMine+=c.spots;
   const tip=`${b} ${String(u).padStart(2,"0")}:00 UTC · listened ${c.listen_h.toFixed(1)} h · ${c.spots} spots · ${c.stations} stations`+(c.max_km?` · best ${c.max_km} km`:"")+(c.snr!=null?` · SNR ${c.snr} dB`:"");
   h+=c.spots?`<span class="c m${cur}" style="opacity:${(0.2+0.8*Math.sqrt(v/top)).toFixed(2)}" title="${esc(tip)}"></span>`:`<span class="c z${cur}" title="${esc(tip)}"></span>`}
  if(showRef)for(let i=0;i<24;i++){const u=hr(i),c=r[u],cur=u===d.now_hour?" cur":"";
   if(!c){h+=`<span class="c${cur}" title="${esc(b)} ${u}:00 UTC - no reference spots"></span>`;continue}
   anyRef+=c.spots;
   const tip=`WSPRnet ${b} ${String(u).padStart(2,"0")}:00 UTC · ${c.spots_per_day} spots/day · ${c.stations} stations · ${c.receivers} receivers`+(c.max_km?` · best ${c.max_km} km`:"")+(c.snr!=null?` · SNR ${c.snr} dB`:"");
   h+=`<span class="c r${cur}" style="opacity:${(0.15+0.85*Math.sqrt(c.spots_per_day/rtop)).toFixed(2)}" title="${esc(tip)}"></span>`}
 }
 $("openG").innerHTML=h;
 const ri=d.ref_info||{};
 $("openInfo").textContent=(opLocal?"local time":"UTC")+" · "+(d.days?"last "+d.days+" days":"all data");
 $("openNote").textContent=showRef?(ri.updated?`Reference: WSPRnet (wspr.live), spots of at least ${ri.min_km} km in a region of about ${Math.round(ri.radius_deg*111)} km around the station, last ${ri.days} days, updated ${new Date(ri.updated*1000).toLocaleString([],{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})}.`:"Reference not downloaded yet (first fetch a few minutes after the start, then once a day)."):"";
}
for(const b of document.querySelectorAll("#openP .tools button[data-d]"))b.addEventListener("click",()=>{opD=+b.dataset.d;
 for(const x of document.querySelectorAll("#openP .tools button[data-d]"))x.classList.toggle("on",x===b);loadOpen(true)});
$("openTz").addEventListener("click",e=>{opLocal=!opLocal;e.target.textContent=opLocal?"Local":"UTC";drawOpen()});
$("openRef").addEventListener("click",e=>{opRef=!opRef;e.target.classList.toggle("on",opRef);drawOpen()});
// ---- waterfall of the last slot of each band ----
let wfBand=null,wfShown=null;
async function loadWf(){
 let d;try{d=await fetch("api/waterfall").then(r=>r.json())}catch(e){return}
 const bands=Object.keys(d.bands||{}).sort((a,b)=>BANDS_ORDER.indexOf(a)-BANDS_ORDER.indexOf(b));
 if(!bands.length){$("wfP").style.display="none";return}
 $("wfP").style.display="";
 if(!wfBand||!d.bands[wfBand])wfBand=bands.reduce((a,b)=>d.bands[b].ts>d.bands[a].ts?b:a);   // default: the newest slot
 $("wfTabs").innerHTML=bands.map(b=>`<button data-b="${esc(b)}" class="${b===wfBand?"on":""}">${esc(b)} · ${ago(d.bands[b].ts)} ago</button>`).join("");
 for(const el of $("wfTabs").querySelectorAll("button"))el.onclick=()=>{wfBand=el.dataset.b;wfShown=null;loadWf()};
 const w=d.bands[wfBand],key=wfBand+w.ts;
 if(key!==wfShown){$("wfImg").src="api/waterfall.png?band="+encodeURIComponent(wfBand)+"&ts="+w.ts;wfShown=key}
 const span=w.f_hi-w.f_lo,rf=(w.dial+1500)/1e6;
 $("wfInfo").textContent=`${w.band} · ${new Date(w.ts*1000).toLocaleString([],{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})} (${fmtT(w.ts).slice(11)} UTC) · ${w.decodes.length} decoded · coverage ${w.coverage} %`;
 // callsign labels at the start of each trace (WSPR starts ~1 s + DT after the even minute)
 $("wfLabels").innerHTML=w.decodes.map(x=>{const left=(x.off_hz-w.f_lo)/span*100,t=1+(x.dt||0)+3,top=Math.max(0,Math.min(92,(t-w.t0)/w.dt/w.rows*100));
  return `<span class="${x.own?"own":""}" style="left:${left.toFixed(2)}%;top:${top.toFixed(1)}%">${esc(x.call)} ${x.snr} dB</span>`}).join("");
 // frequency axis: every 50 Hz, labels as audio offset and the real frequency at the centre
 let ax="";for(let f=-150;f<=150;f+=50){const l=(f-w.f_lo)/span*100;const tx=f<=-150?"0":(f>=150?"-100%":"-50%");ax+=`<span style="left:${l}%;transform:translateX(${tx})">${f===0?rf.toFixed(4)+" MHz":(f>0?"+":"")+f+" Hz"}</span>`}
 $("wfAxis").innerHTML=ax;
 let tm="";for(const t of [0,30,60,90,120]){const r=Math.max(0,Math.min(100,(t-w.t0)/w.dt/w.rows*100));tm+=`<span style="top:${r}%">${t}s</span>`}
 $("wfTime").innerHTML=tm;
}
async function load(){
 try{
  const pv=$("per").value, all=pv==="0", hrs=all?24*3650:(+pv||24);   // "all" = 10 years
  const per=all?"all time":$("per").selectedOptions[0].textContent;    // the label as shown in the selector ("last 7 days", not "last 168 h")
  const [st,sm,sp,sy]=await Promise.all([fetch("api/status").then(r=>r.json()),fetch("api/summary?hours="+hrs+($("noOwn").checked?"&noown=1":"")).then(r=>r.json()),fetch("api/spots?"+q()+"&limit=1000").then(r=>r.json()),fetch("api/system").then(r=>r.json()).catch(()=>({}))]);
  const s=st.station,m=st.map;
  document.body.classList.toggle("pub",!st.auth);document.body.classList.toggle("auth",!!st.auth);
  $("who").innerHTML=esc(s.callsign)+" · "+(m?`<a href="${esc(m)}" target="_blank" rel="noopener">${esc(s.grid)}</a>`:esc(s.grid));
  $("hrsB").textContent=per;
  let cards="";
  for(const r of st.radios){
    const cls=r.state==="listening"?"ok":(r.state==="error"?"bad":"mute");
    cards+=`<div class="card"><div class="k">${hq("radio")}${esc(r.name)}</div><div class="v ${cls}">${esc(r.state)}</div><div class="s">${esc(r.band||"-")}${r.antenna?" · ant "+esc(r.antenna):""}${r.next_ts?" · next slot in "+Math.max(0,Math.round(r.next_ts-Date.now()/1000))+" s":""}</div></div>`}
  const ls=sm.last_slot;
  let lv="-",lcls="mute",lsub="";
  if(ls&&ls.peak!=null){lv=(ls.peak*100).toFixed(0)+" %";lcls=ls.peak>0.9?"bad":(ls.peak>0.5?"warn":"ok");lsub=ls.band+" · mean "+(ls.rms_db||0).toFixed(0)+" dB"+(ls.peak>0.9?" · OVERLOAD: lower the gain":"")}
  cards+=`<div class="card"><div class="k">${hq("level")}Input level (peak)</div><div class="v ${lcls}">${lv}</div><div class="s">${esc(lsub)}</div></div>`;
  if(sy&&sy.temp_c!=null){                                      // computer health: temperature, clock, power, slot coverage
   const ev=sy.uv_events_1h, cov=sy.coverage_1h;
   const pw=sy.power_data?(sy.uv_now?"UNDER-VOLTAGE now":(ev>0?ev+" UV/h":"power OK")):"";
   const bad=sy.uv_now||ev>=10||(cov!=null&&cov<95)||sy.temp_c>75, warn=ev>0||(cov!=null&&cov<100)||sy.temp_c>65;
   const tip=[sy.pi||"",sy.throttled_hex?"throttled "+sy.throttled_hex:"",sy.temp_max_1h!=null?"max "+sy.temp_max_1h.toFixed(1)+" °C in 1 h":"",
     sy.low_clock_pct_1h!=null?"CPU below 1 GHz "+sy.low_clock_pct_1h+" % of 1 h":"",sy.slots_1h?sy.partial_1h+" of "+sy.slots_1h+" slots incomplete in 1 h":""].filter(Boolean).join(" · ");
   cards+=`<div class="card" title="${esc(tip)}"><div class="k">${hq("system")}System</div><div class="v ${bad?"bad":(warn?"warn":"ok")}">${sy.temp_c.toFixed(0)} °C</div><div class="s">${sy.clock_mhz?sy.clock_mhz+" MHz":""}${pw?" · "+esc(pw):""}${cov!=null?" · coverage "+cov.toFixed(0)+" %":""}</div></div>`}
  cards+=`<div class="card"><div class="k">${hq("spots")}Spots</div><div class="v">${sm.spots}</div><div class="s">${per}</div></div>`;
  cards+=`<div class="card"><div class="k">${hq("stations")}Stations</div><div class="v">${sm.calls}</div><div class="s">different callsigns</div></div>`;
  const bd=sm.best;
  cards+=`<div class="card"><div class="k">${hq("bestdx")}Best DX · ${all?"all":esc(per)}</div><div class="v">${bd?Math.round(bd.dist_km)+" km":"-"}</div><div class="s">${bd?esc(bd.call)+" · "+esc(bd.grid)+" · "+esc(bd.band):"no DX in this period"}</div></div>`;
  const ba=sm.best_all;
  cards+=`<div class="card"><div class="k">${hq("bestall")}Best DX · all time</div><div class="v">${ba?Math.round(ba.dist_km)+" km":"-"}</div><div class="s">${ba?esc(ba.call)+" · "+esc(ba.grid)+" · "+esc(ba.band)+"<br>"+esc(new Date(ba.ts*1000).toISOString().slice(0,16).replace("T"," "))+" UTC · "+ba.snr+" dB":""}</div></div>`;
  $("cards").innerHTML=cards;
  loadWf();loadHist();loadOpen();
  let h="<thead><tr><th>Band</th><th>Slots</th><th>Spots</th><th>Stations</th><th>Last slot</th></tr></thead><tbody>";
  for(const b of sm.bands)h+=`<tr><td>${esc(b.band)}</td><td>${b.slots}</td><td>${b.spots}</td><td>${b.calls}</td><td>${ago(b.last_ts)} ago</td></tr>`;
  $("bands").innerHTML=h+"</tbody>";
  const sel=$("bandSel"),cur=sel.value;
  if(sel.options.length<=1){for(const b of st.bands){const o=document.createElement("option");o.value=b;o.textContent=b;sel.appendChild(o)}sel.value=cur}
  h=`<thead><tr><th>Time (UTC)${hq("time")}</th><th>Band${hq("band")}</th><th>Freq (MHz)${hq("freq")}</th><th>SNR${hq("snr")}</th><th>DT${hq("dt")}</th><th>Drift${hq("drift")}</th><th class='l'>Call${hq("call")}</th><th class='l'>Locator${hq("loc")}</th><th>Power${hq("power")}</th><th>Dist (km)${hq("dist")}</th><th>Brg${hq("brg")}</th></tr></thead><tbody>`;
  for(const r of sp.spots){
   const loc=r.map?`<a href="${esc(r.map)}" target="_blank" rel="noopener" title="open in Google Maps">${esc(r.grid)}</a>`:esc(r.grid);
   h+=`<tr><td title="${esc(new Date(r.ts*1000).toLocaleString())}">${fmtT(r.ts)}</td><td>${esc(r.band)}</td><td>${(r.freq_hz/1e6).toFixed(6)}</td><td>${r.snr}</td><td>${r.dt==null?"":r.dt.toFixed(1)}</td><td>${r.drift}</td><td class="l"><b>${esc(r.call)}</b></td><td class="l">${loc}</td><td>${r.power_dbm==null?"":r.power_dbm+" dBm"}</td><td>${r.dist_km==null?"":Math.round(r.dist_km)}</td><td>${r.bearing==null?"":Math.round(r.bearing)+"°"}</td></tr>`}
  if(!sp.spots.length)h+="<tr><td colspan=11 class='l mute'>no spots in this period yet</td></tr>";
  $("spots").innerHTML=h+"</tbody>";
  $("cnt").textContent=sp.spots.length+(sp.spots.length>=1000?"+":"")+" shown";
  try{drawMap(sp.spots,st)}catch(e){console.error(e)}
  $("upd").textContent="updated "+new Date().toLocaleTimeString();
 }catch(e){$("upd").textContent="no connection"}
}
for(const id of ["per","bandSel"])$(id).addEventListener("change",load);
try{$("noOwn").checked=localStorage.getItem("wsm_noown")==="1"}catch(e){}
$("noOwn").addEventListener("change",()=>{try{localStorage.setItem("wsm_noown",$("noOwn").checked?"1":"0")}catch(e){};mapKey=null;load()});
let tm;$("callQ").addEventListener("input",()=>{clearTimeout(tm);tm=setTimeout(load,400)});
$("csv").addEventListener("click",()=>{location.href="spots.csv?"+q()+"&limit=100000"});
$("th").addEventListener("click",()=>{const r=document.documentElement,d=r.dataset.theme==="dark"||(!r.dataset.theme&&matchMedia("(prefers-color-scheme: dark)").matches);r.dataset.theme=d?"light":"dark";try{localStorage.setItem("wsm_theme",r.dataset.theme)}catch(e){}});
try{const t=localStorage.getItem("wsm_theme");if(t)document.documentElement.dataset.theme=t}catch(e){}
load();setInterval(load,20000);
</script></body></html>
"""


FAVICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0b6b8a"/><g fill="none" stroke="#fff" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"><path d="M32 27 22 56M32 27 42 56M26.5 44h11"/><path d="M25.6 15.6A9 9 0 0 0 25.6 28.4M38.4 15.6A9 9 0 0 1 38.4 28.4"/><path d="M20 10A17 17 0 0 0 20 34M44 10A17 17 0 0 1 44 34"/></g><circle cx="32" cy="22" r="4.5" fill="#fff"/></svg>'


class State:
    """what the service tells the web page about the radios"""

    def __init__(self, cfg, bands):
        self.cfg = cfg
        self.bands = bands
        self.radios = {r["name"]: dict(name=r["name"], state="starting", band=None, antenna=r["antenna"], next_ts=None)
                       for r in cfg["radios"]}
        self.lock = threading.Lock()
        self.started = time.time()

    def set(self, radio, **kw):
        with self.lock:
            self.radios[radio].update(kw)

    def snapshot(self):
        with self.lock:
            return [dict(v) for v in self.radios.values()]


class Handler(BaseHTTPRequestHandler):
    server_version = "WSM"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "geolocation=(), camera=(), microphone=()")   # PATCH9
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if self._https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org")
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj):
        self._send(200, json.dumps(obj, separators=(",", ":"), default=str), "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _client(self):
        """client address: CF-Connecting-IP only when the TCP peer is this machine (the Cloudflare tunnel)"""
        peer = self.client_address[0]
        if peer in ("127.0.0.1", "::1"):
            return (self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0] or peer).strip()[:64]
        return peer

    def _https(self):
        return self.client_address[0] in ("127.0.0.1", "::1") and (
            self.headers.get("X-Forwarded-Proto", "").lower() == "https" or '"https"' in self.headers.get("CF-Visitor", ""))

    def _authorized(self, q):                                    # PATCH9
        tok = self.server.token
        if not tok:
            return True, None
        ip, now = self._client(), time.time()
        fails = [t for t in self.server.fails.get(ip, []) if now - t < 600]
        sess = self.server.session
        got = ""
        for c in self.headers.get("Cookie", "").split(";"):
            k, _, v = c.strip().partition("=")
            if k == "wsm_key":
                got = v
        if got and hmac.compare_digest(got, sess):
            return True, None
        if len(fails) >= 8:
            self.server.fails[ip] = fails
            return None, None                                    # locked out (a valid cookie still works)
        key = q.get("key", [""])[0] or self.headers.get("X-WSM-Key", "")
        if key and hmac.compare_digest(key.encode(), tok.encode()):
            self.server.fails.pop(ip, None)
            return True, f"wsm_key={sess}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000" + ("; Secure" if self._https() else "")
        if key:                                                  # only a wrong key counts (not a bare visit, not an old cookie)
            fails.append(now)
            if len(self.server.fails) > 2000:
                self.server.fails.clear()
            self.server.fails[ip] = fails
            if len(fails) == 8:
                self.server.log(f"web: too many wrong keys from {ip}; locked out for 10 minutes")
        return False, None

    def _page(self, name):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", name), "rb") as f:
            return self._send(200, f.read(), "text/html; charset=utf-8", {"Cache-Control": "no-cache"})

    def _static(self, name):
        types = {"leaflet.js": "application/javascript; charset=utf-8", "leaflet.css": "text/css; charset=utf-8"}
        if name not in types:
            return self._send(404, "not found", "text/plain; charset=utf-8")
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", name), "rb") as f:
            return self._send(200, f.read(), types[name], {"Cache-Control": "public, max-age=86400"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/favicon.svg", "/favicon.ico"):
                return self._send(200, FAVICON_SVG, "image/svg+xml", {"Cache-Control": "public, max-age=86400"})
            if u.path in ("/about", "/about/", "/wspr"):                   # public: the "What is WSPR?" page (no station data in it)
                return self._page("about.html")
            land = self.server.cfg.get("landing") or {}
            if _yes(land.get("enabled")):                                # public station landing page (its own host name)
                host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
                if u.path == "/api/landing":
                    return self._json(wsm_landing.landing(self.server.cfg, self.server.db, self.server.state))
                if host and host in [str(h).lower() for h in land.get("hosts") or []]:
                    if u.path in ("/", "/index.html"):
                        return self._page("station.html")
                    return self._send(404, "not found", "text/plain; charset=utf-8")
            ok, setcookie = self._authorized(q)
            if ok is None:
                return self._send(429, "too many attempts", "text/plain; charset=utf-8", {"Retry-After": "600"})
            authed = bool(ok)
            if not ok:
                if not (self.server.public and (u.path in PUBLIC_PATHS or u.path.startswith("/static/"))):
                    return self._send(401, "restricted access", "text/plain; charset=utf-8")
            if setcookie:
                return self._send(303, "", "text/plain", {"Location": "/", "Set-Cookie": setcookie})
            db, st, p = self.server.db, self.server.state, u.path

            def num(name, default, lo, hi):
                try:
                    return max(lo, min(hi, int(float(q.get(name, [default])[0]))))
                except ValueError:
                    return default

            if p.startswith("/static/"):
                return self._static(p[8:])
            if p in ("/", "/index.html"):
                return self._send(200, PAGE, "text/html; charset=utf-8", {"Cache-Control": "no-cache"})
            if p == "/api/status":
                cfg = st.cfg["station"]
                ll = wsm_geo.grid_to_latlon(cfg["grid"])
                station = cfg
                if not authed:                                   # public visitor: callsign, locator and a 6-character square only
                    g6 = wsm_wsprnet.grid6(cfg.get("lat"), cfg.get("lon"))
                    c6 = wsm_geo.grid_to_latlon(g6) if g6 else None
                    station = dict(callsign=cfg["callsign"], grid=cfg["grid"])
                    if c6:
                        station.update(lat=round(c6[0], 3), lon=round(c6[1], 3))
                return self._json(dict(station=station, map=wsm_geo.maps_url(*ll) if ll else None, radios=st.snapshot(), bands=st.bands,
                                       uptime_s=int(time.time() - st.started), auth=authed, public=self.server.public))
            own = st.cfg["station"]["callsign"]
            noown = (q.get("noown") or ["0"])[0] in ("1", "true", "yes")
            if p == "/api/summary":
                hours = num("hours", 24, 1, 24 * 3650)
                return self._json(db.summary(time.time() - hours * 3600, st.cfg["station"]["callsign"], hide_own=noown))
            if p == "/api/waterfall":                                # last waterfall of each band: metadata + decodes
                wfs = getattr(st, "waterfalls", None) or {}
                return self._json(dict(bands={b: {k: v for k, v in w.items() if k != "png"} for b, w in list(wfs.items())}))
            if p == "/api/waterfall.png":
                w = (getattr(st, "waterfalls", None) or {}).get((q.get("band") or [""])[0])
                if not w:
                    return self._send(404, "no waterfall for this band yet", "text/plain; charset=utf-8")
                return self._send(200, w["png"], "image/png", {"Cache-Control": "no-cache"})
            if p == "/api/openings":                                 # band x hour matrix, mine + WSPRnet reference
                import wsm_openings
                return self._json(wsm_openings.payload(st.cfg, db, num("days", 30, 0, 3650)))
            if p == "/api/history":                                  # health graphs (never public)
                hours = num("hours", 24, 1, 24 * 400)
                bucket = 300 if hours <= 48 else (1800 if hours <= 24 * 8 else 7200)
                h = db.history(time.time() - hours * 3600, bucket)
                hc = st.cfg.get("health") or {}
                h.update(hours=hours, temp_max_c=float(hc.get("temp_max_c", 75)), coverage_min=float(hc.get("coverage_min", 95)))
                return self._json(h)
            if p == "/api/system":
                sm = getattr(st, "sysmon", None)
                return self._json(sm.snapshot() if sm else {})
            if p == "/api/spots":
                return self._json(dict(spots=db.spots(num("since", 0, 0, 2 ** 40), (q.get("band") or [""])[0] or None,
                                                      (q.get("call") or [""])[0] or None, num("limit", 500, 1, 100000 if authed else 1000),
                                                      own if noown else None)))
            if p == "/api/slots":
                return self._json(dict(slots=db.recent_slots(num("limit", 12, 1, 500))))
            if p == "/spots.csv":
                rows = db.spots(num("since", 0, 0, 2 ** 40), (q.get("band") or [""])[0] or None, (q.get("call") or [""])[0] or None,
                                num("limit", 100000, 1, 1000000), own if noown else None)
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["time_utc", "band", "freq_hz", "snr_db", "dt_s", "drift", "call", "locator", "power_dbm", "dist_km", "bearing", "maps"])
                for r in rows:
                    w.writerow([time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["ts"])), r["band"], f"{r['freq_hz']:.0f}", r["snr"], r["dt"], r["drift"],
                                r["call"], r["grid"], r["power_dbm"], None if r["dist_km"] is None else round(r["dist_km"]),
                                None if r["bearing"] is None else round(r["bearing"]), r.get("map", "")])
                return self._send(200, buf.getvalue(), "text/csv; charset=utf-8", {"Content-Disposition": "attachment; filename=wsm_spots.csv"})
            return self._send(404, "not found", "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                                   # a bad request must never take the page down
            try:
                self._send(500, f"error: {type(e).__name__}", "text/plain; charset=utf-8")
            except Exception:
                pass


PUBLIC_PATHS = {"/", "/index.html", "/api/status", "/api/summary", "/api/openings", "/api/spots", "/api/waterfall", "/api/waterfall.png"}


def _yes(v):
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(cfg, db, state, log=print):
    w = cfg["web"]
    srv = Server((w["host"], int(w["port"])), Handler)
    srv.db, srv.state, srv.token = db, state, (w.get("token") or "").strip()
    srv.fails, srv.log = {}, log                                 # PATCH9
    srv.cfg, srv.public = cfg, _yes(w.get("public")) and bool(srv.token)
    if srv.public:
        log("web: public mode - visitors without the key see spots, map and waterfall (not health, not CSV)")
    srv.session = hmac.new(srv.token.encode(), b"wsm-session-v1", hashlib.sha256).hexdigest()
    if not srv.token:
        log("web: WARNING no web.token set - the page is open to everybody who can reach it")
    elif len(srv.token) < 16:
        log("web: WARNING web.token is shorter than 16 characters")
    try:
        for f in ("wsm.yaml",):
            path = os.path.join(os.getcwd(), f)
            if os.path.exists(path) and os.stat(path).st_mode & 0o044:
                log(f"web: WARNING {f} is readable by other users (it holds secrets): chmod 600 {path}")
    except OSError:
        pass
    threading.Thread(target=srv.serve_forever, name="wsm-web", daemon=True).start()
    log(f"web page at http://{w['host']}:{w['port']}/" + ("  (with key)" if srv.token else ""))
    return srv
