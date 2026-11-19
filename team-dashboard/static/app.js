// Project D Team Dashboard v1 — renders /api/summary. No third-party code; values are inserted as text.
"use strict";

const REFRESH_MS = 10000;
const LABEL = { ok: "OK", out_of_range: "Out of range", silent: "Silent", no_data: "No data" };
const TYPE = { temperature: "Temperature", humidity: "Humidity", airquality: "Air quality (PM2.5)" };
let lastGood = null;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatAge(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))} s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

function sparkline(sensor) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "spark");
  svg.setAttribute("viewBox", "0 0 300 54");
  svg.setAttribute("preserveAspectRatio", "none");
  const points = sensor.history || [];
  if (points.length < 2) {
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", "4"); t.setAttribute("y", "30"); t.setAttribute("class", "empty");
    t.textContent = "no data in the last hour";
    svg.appendChild(t);
    return svg;
  }
  const values = points.map(p => p[1]);
  let min = Math.min(...values), max = Math.max(...values);
  // Show the allowed band when it is near the data, so drift towards a threshold is visible.
  if (sensor.low > min - (max - min) * 2) min = Math.min(min, sensor.low);
  if (sensor.high < max + (max - min) * 2) max = Math.max(max, sensor.high);
  if (max - min < 1e-6) { max += 1; min -= 1; }
  const t0 = Date.parse(points[0][0]), t1 = Date.parse(points[points.length - 1][0]) || t0 + 1;
  const x = t => 2 + 296 * (Date.parse(t) - t0) / Math.max(1, t1 - t0);
  const y = v => 50 - 46 * (v - min) / (max - min);
  const band = document.createElementNS(ns, "rect");
  const top = y(Math.min(sensor.high, max)), bottom = y(Math.max(sensor.low, min));
  band.setAttribute("x", "0"); band.setAttribute("width", "300");
  band.setAttribute("y", String(Math.min(top, bottom))); band.setAttribute("height", String(Math.abs(bottom - top)));
  band.setAttribute("class", "band");
  svg.appendChild(band);
  const line = document.createElementNS(ns, "polyline");
  line.setAttribute("class", "line");
  line.setAttribute("points", points.map(p => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" "));
  svg.appendChild(line);
  const title = document.createElementNS(ns, "title");
  title.textContent = `Last hour, 1-minute means. Allowed range ${sensor.low} to ${sensor.high} ${sensor.unit}.`;
  svg.appendChild(title);
  return svg;
}

function card(sensor) {
  const box = el("article", `card ${sensor.status}`);
  const left = el("div");
  left.appendChild(el("div", "name", sensor.sensor_id));
  left.appendChild(el("div", "type", TYPE[sensor.sensor_type] || sensor.sensor_type));
  box.appendChild(left);
  const value = el("div", "value", sensor.value === null ? "—" : sensor.value.toFixed(1));
  value.appendChild(el("span", "unit", sensor.unit));
  box.appendChild(value);
  const delay = sensor.delay_ms_10m === null ? "delay n/a" : `delay ${sensor.delay_ms_10m.toFixed(0)} ms (10 min mean)`;
  const range = sensor.low < 0 ? `max ${sensor.high}` : `range ${sensor.low}–${sensor.high}`;
  box.appendChild(el("div", "meta", `${formatAge(sensor.age_seconds)} · ${delay} · ${range}`));
  box.appendChild(el("span", `badge ${sensor.status}`, LABEL[sensor.status] || sensor.status));
  box.appendChild(sparkline(sensor));
  return box;
}

function render(data, stale) {
  for (const section of document.querySelectorAll(".city")) {
    const cards = section.querySelector(".cards");
    cards.replaceChildren(...data.sensors.filter(s => s.city === section.dataset.city).map(card));
  }
  const overall = document.getElementById("overall");
  const problems = data.sensors.length - (data.counts.ok || 0);
  overall.className = `pill pill-${stale ? "error" : data.overall}`;
  overall.textContent = stale ? "Data source unavailable" : (problems === 0 ? "All 6 sensors OK" : `${problems} of 6 need attention`);
  document.getElementById("updated").textContent = `Updated ${new Date(data.generated_at).toLocaleTimeString()}${stale ? " (stale)" : ""}`;
  document.getElementById("silent-after").textContent = String(data.silent_after_seconds);
}

async function refresh() {
  const banner = document.getElementById("banner");
  try {
    const response = await fetch("/api/summary", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.status);
    lastGood = data;
    banner.hidden = true;
    render(data, false);
  } catch (err) {
    banner.hidden = false;
    banner.textContent = `Could not load live data (${err.message}). ${lastGood ? "Showing the last good values." : ""}`;
    if (lastGood) render(lastGood, true);
    else {
      const overall = document.getElementById("overall");
      overall.className = "pill pill-error";
      overall.textContent = "Data source unavailable";
    }
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
