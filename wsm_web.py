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
table{border-collapse:collapse;width:100%;min-width:720px}
th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mute);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
th:first-child,td:first-child,th.l,td.l{text-align:left}
tr:hover td{background:color-mix(in srgb,var(--chip) 60%,transparent)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--chip);font-size:12px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.mute{color:var(--mute)}
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
<header><h1>WSM <small>WSPR System Monitor</small></h1><span id="who" class="mute"></span>
<span style="flex:1"></span><a href="/about" style="color:var(--acc);text-decoration:none;font-weight:600;margin-right:12px">What is WSPR?</a><span id="upd" class="mute"></span></header>
<div class="cards" id="cards"></div>
<div class="panel" id="wfP" style="display:none"><h2><b class="ht">Last slot · waterfall<button class="hq" data-h="wf" aria-label="explain">?</button></b><span id="wfInfo"></span></h2>
<div class="tools" id="wfTabs"></div>
<div id="wfBox"><div id="wfTime"></div><div id="wfImgW"><img id="wfImg" alt="waterfall of the last slot"><div id="wfLabels"></div></div><div id="wfAxis"></div></div>
<p class="note">What the receiver heard in the last 2-minute slot of the band: frequency across (the 200 Hz WSPR window), time downwards.
Each WSPR station is a thin vertical trace about 110 s long; the decoded ones are labelled with their callsign and SNR. Brighter = stronger.</p></div>
<div class="panel" id="bandsP"><h2><b class="ht">Bands<button class="hq" data-h="bands" aria-label="explain">?</button></b><span id="hrsB">last 24 h</span></h2><div class="tw"><table id="bands"></table></div></div>
<div class="panel" id="mapP"><h2><b class="ht">Map<button class="hq" data-h="map" aria-label="explain">?</button></b><span id="mapInfo"></span></h2>
<div class="tools"><button id="mLines" class="on" title="great-circle lines from the station">lines</button><button id="mRings" title="distance rings">rings</button><button id="mFit" title="zoom to all spots">fit</button><span id="legend"></span></div>
<div id="map"></div>
<p class="note">Shows the spots chosen below (period, band, callsign). One dot per station and band, placed at the centre of its 4-character locator square (about +-100 km),
coloured by band; tap a dot for the details. Map data &copy; OpenStreetMap contributors.</p></div>
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
</main>
<div id="tip" role="tooltip" hidden></div>
<script src="static/leaflet.js"></script>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// ---- "?" explanations: tap or click a "?" next to a title ----
const HELP={
 radio:"State of the receiver. <b>listening</b> = recording a 2-minute slot; <b>opening</b> = starting the radio; <b>error</b> = see the log. Below: band of this slot, antenna input and time to the next slot. WSM listens to one band per slot, following the rotation (or the day/night schedule) in wsm.yaml.",
 level:"Strongest sample of the last slot, as a share of the radio's full scale. Keep it <b>below about 50 %</b>; above 90 % the receiver overloads and invents false signals, so lower <b>gain_db</b>. <b>mean</b> is the average power in dB (relative): useful to compare the noise of bands and antennas.",
 system:"Health of the computer: CPU temperature, CPU clock (a Raspberry Pi slows down when hot or under-powered), power supply and <b>coverage</b>, the share of the samples actually recorded in the slots of the last hour. Below 100 % the computer is losing samples and weak stations are missed. Hover the card for details.",
 spots:"A <b>spot</b> is one decoded transmission: one station heard in one 2-minute slot. A station heard in 10 slots gives 10 spots. The period is the one chosen above the spot list.",
 stations:"How many <b>different callsigns</b> were decoded in the period.",
 bestdx:"The farthest station heard in the period: distance from this station to the centre of the other station's locator square (about +-100 km). Your own transmitter never counts.",
 bestall:"The farthest station this receiver has ever heard, with band, date and SNR.",
 wf:"A picture of the last 2-minute slot of a band, from the same samples the decoder used. Frequency goes across (+-150 Hz around the centre of the WSPR window), time downwards. Each WSPR transmission is a thin, slightly wavy vertical line about 110 s long, starting 1 s after the even minute. Decoded ones are labelled. A line from top to bottom (0 to 120 s), perfectly straight, is not WSPR but a steady carrier or interference.",
 bands:"For each band in the period: <b>Slots</b> = 2-minute recordings made, <b>Spots</b> = transmissions decoded, <b>Stations</b> = different callsigns, <b>Last slot</b> = when the band was last listened to. Many slots and no spots = band closed at that time, or the antenna is weak there.",
 map:"Every station of the spot list below, placed at the centre of its locator square and coloured by band. Lines are great-circle paths, the shortest way over the globe, roughly the way the signal travelled. Tap a dot for details.",
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
  LAYER=L.layerGroup().addTo(MAP)}
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
// ---- waterfall of the last slot of each band ----
let wfBand=null,wfShown=null;
async function loadWf(){
 let d;try{d=await fetch("api/waterfall").then(r=>r.json())}catch(e){return}
 const bands=Object.keys(d.bands||{}).sort((a,b)=>BANDS_ORDER.indexOf(a)-BANDS_ORDER.indexOf(b));
 if(!bands.length){$("wfP").style.display="none";return}
 $("wfP").style.display="";
 if(!wfBand||!d.bands[wfBand])wfBand=bands.reduce((a,b)=>d.bands[b].ts>d.bands[a].ts?b:a);   // default: the newest slot
 $("wfTabs").innerHTML=bands.map(b=>`<button data-b="${esc(b)}" class="${b===wfBand?"on":""}">${esc(b)} · ${fmtT(d.bands[b].ts).slice(11)}Z</button>`).join("");
 for(const el of $("wfTabs").querySelectorAll("button"))el.onclick=()=>{wfBand=el.dataset.b;wfShown=null;loadWf()};
 const w=d.bands[wfBand],key=wfBand+w.ts;
 if(key!==wfShown){$("wfImg").src="api/waterfall.png?band="+encodeURIComponent(wfBand)+"&ts="+w.ts;wfShown=key}
 const span=w.f_hi-w.f_lo,rf=(w.dial+1500)/1e6;
 $("wfInfo").textContent=`${w.band} · ${fmtT(w.ts)} UTC · ${w.decodes.length} decoded · coverage ${w.coverage} %`;
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
  loadWf();
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
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "about.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8", {"Cache-Control": "no-cache"})
            ok, setcookie = self._authorized(q)
            if ok is None:
                return self._send(429, "too many attempts", "text/plain; charset=utf-8", {"Retry-After": "600"})
            if not ok:
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
                return self._json(dict(station=cfg, map=wsm_geo.maps_url(*ll) if ll else None, radios=st.snapshot(), bands=st.bands,
                                       uptime_s=int(time.time() - st.started)))
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
            if p == "/api/system":
                sm = getattr(st, "sysmon", None)
                return self._json(sm.snapshot() if sm else {})
            if p == "/api/spots":
                return self._json(dict(spots=db.spots(num("since", 0, 0, 2 ** 40), (q.get("band") or [""])[0] or None,
                                                      (q.get("call") or [""])[0] or None, num("limit", 500, 1, 100000), own if noown else None)))
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


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(cfg, db, state, log=print):
    w = cfg["web"]
    srv = Server((w["host"], int(w["port"])), Handler)
    srv.db, srv.state, srv.token = db, state, (w.get("token") or "").strip()
    srv.fails, srv.log = {}, log                                 # PATCH9
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
