import React, { useEffect, useMemo, useRef, useState } from 'react';
import { compactNumber, formatValue } from '../api';

// Single-series charts drawn as SVG. Marks use the chart gold (#a87600, passes
// 3:1 on white); text uses text tokens; gridlines are 1px and recessive.
const MARK = '#a87600';
const GRID = '#ece9e4';
const INK = '#504534';
const MUTED = '#827562';
const SURFACE = '#ffffff';

function niceTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const norm = step0 / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const start = Math.floor(min / step) * step;
  const ticks = [];
  // Always end on a tick at or above max so no mark overshoots the axis.
  for (let v = start; ; v += step) {
    ticks.push(Number(v.toPrecision(12)));
    if (v >= max - step * 1e-9 || ticks.length > 50) break;
  }
  return ticks;
}

function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

const toNum = (v, temporal) => (temporal ? Date.parse(typeof v === 'string' && v.length === 10 ? `${v}T00:00:00` : v) : Number(v));

function fmtAxis(v, temporal, span) {
  if (temporal) {
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return '';
    const day = 86400000;
    if (span > 2 * 365 * day) return d.toISOString().slice(0, 4);
    if (span > 60 * day) return d.toISOString().slice(0, 7);
    if (span > 2 * day) return d.toISOString().slice(0, 10);
    return d.toISOString().slice(11, 16);
  }
  return compactNumber(v);
}

// Rounded data-end (4px) and square baseline.
function barPath(x, y, w, h) {
  if (h <= 0 || w <= 0) return '';
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

export default function Chart({ data, height = 360, xLabel, yLabel }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const m = { top: 16, right: 24, bottom: data?.type === 'bar' ? 72 : 44, left: 64 };
  const iw = width - m.left - m.right;
  const ih = height - m.top - m.bottom;

  const model = useMemo(() => {
    if (!data) return null;
    const temporal = data.x_type === 'temporal';
    if (data.type === 'histogram') {
      const bins = (data.bins || []).map(b => ({ x0: toNum(b.x0, temporal), x1: toNum(b.x1, temporal), count: b.count, raw: b }));
      if (!bins.length) return { empty: true };
      const xmin = bins[0].x0;
      const xmax = bins[bins.length - 1].x1 === xmin ? xmin + 1 : bins[bins.length - 1].x1;
      const yt = niceTicks(0, Math.max(...bins.map(b => b.count)));
      return { kind: 'hist', temporal, bins, xmin, xmax, xt: niceTicks(xmin, xmax, 6), yt };
    }
    if (data.type === 'bar') {
      const pts = (data.points || []).map(p => ({ ...p, y: p.y ?? 0 }));
      if (!pts.length) return { empty: true };
      const ymin = Math.min(0, ...pts.map(p => p.y));
      const yt = niceTicks(ymin, Math.max(0, ...pts.map(p => p.y)));
      return { kind: 'bar', pts, yt };
    }
    const pts = (data.points || []).map(p => ({ x: toNum(p.x, temporal), y: p.y, raw: p })).filter(p => Number.isFinite(p.x) && p.y !== null && p.y !== undefined);
    if (!pts.length) return { empty: true };
    const xs = pts.map(p => p.x);
    const ys = pts.map(p => p.y);
    const xt = niceTicks(Math.min(...xs), Math.max(...xs), 6);
    const yt = niceTicks(Math.min(...ys), Math.max(...ys));
    return { kind: data.type, temporal, pts, xt, yt };
  }, [data]);

  if (!data) return null;
  if (!model || model.empty) {
    return <div className="flex h-[200px] items-center justify-center text-on-surface-variant font-ui-label">No values to plot</div>;
  }

  const yt = model.yt;
  const y0 = yt[0];
  const y1 = yt[yt.length - 1];
  const sy = v => m.top + ih - ((v - y0) / (y1 - y0 || 1)) * ih;

  let marks = null;
  let xAxis = null;
  let overlay = null;

  if (model.kind === 'hist') {
    const xr = model.xmax - model.xmin || 1;
    const sx = v => m.left + ((v - model.xmin) / xr) * iw;
    marks = model.bins.map((b, i) => {
      const x = sx(b.x0) + 1;
      const w = Math.max(1, sx(b.x1) - sx(b.x0) - 2); // 2px surface gap between touching bars
      const top = sy(b.count);
      return (
        <g key={i} onMouseEnter={() => setHover({ x: sx((b.x0 + b.x1) / 2), y: top, lines: [`${formatValue(b.raw.x0, model.temporal ? 'Datetime' : '')} – ${formatValue(b.raw.x1, model.temporal ? 'Datetime' : '')}`, `${b.count.toLocaleString()} rows`] })}
          onMouseLeave={() => setHover(null)}>
          <rect x={sx(b.x0)} y={m.top} width={Math.max(1, sx(b.x1) - sx(b.x0))} height={ih} fill="transparent" />
          <path d={barPath(x, top, w, sy(y0) - top)} fill={MARK} opacity={hover && hover.x !== sx((b.x0 + b.x1) / 2) ? 0.55 : 1} />
        </g>
      );
    });
    const ticks = model.temporal ? niceTicks(model.xmin, model.xmax, 6) : model.xt;
    xAxis = ticks.filter(t => t >= model.xmin && t <= model.xmax).map(t => (
      <text key={t} x={sx(t)} y={m.top + ih + 18} textAnchor="middle" fontSize="11" fill={MUTED}>{fmtAxis(t, model.temporal, xr)}</text>
    ));
  } else if (model.kind === 'bar') {
    const n = model.pts.length;
    const band = iw / n;
    const bw = Math.min(24, Math.max(2, band - 2));
    const rotate = n > 8 || model.pts.some(p => String(p.x).length > 10);
    marks = model.pts.map((p, i) => {
      const cx = m.left + band * i + band / 2;
      const top = sy(Math.max(p.y, 0));
      const bottom = sy(Math.min(p.y, 0));
      return (
        <g key={i} onMouseEnter={() => setHover({ x: cx, y: top, lines: [formatValue(p.x), `${data.agg === 'count' && !data.y ? 'rows' : `${data.agg} of ${data.y}`}: ${formatValue(p.y)}`] })}
          onMouseLeave={() => setHover(null)}>
          <rect x={m.left + band * i} y={m.top} width={band} height={ih} fill="transparent" />
          <path d={barPath(cx - bw / 2, top, bw, bottom - top)} fill={MARK} opacity={hover && hover.x !== cx ? 0.55 : 1} />
        </g>
      );
    });
    xAxis = model.pts.map((p, i) => {
      const cx = m.left + band * i + band / 2;
      const label = p.x === null ? 'null' : String(p.x);
      const short = label.length > 14 ? `${label.slice(0, 13)}…` : label;
      if (n > 30 && i % Math.ceil(n / 30)) return null;
      return rotate ? (
        <text key={i} transform={`translate(${cx},${m.top + ih + 12}) rotate(-40)`} textAnchor="end" fontSize="11" fill={MUTED}>{short}</text>
      ) : (
        <text key={i} x={cx} y={m.top + ih + 18} textAnchor="middle" fontSize="11" fill={MUTED}>{short}</text>
      );
    });
  } else {
    const xt = model.xt;
    const x0 = xt[0];
    const x1 = xt[xt.length - 1];
    const sx = v => m.left + ((v - x0) / (x1 - x0 || 1)) * iw;
    const span = x1 - x0;
    xAxis = xt.map(t => <text key={t} x={sx(t)} y={m.top + ih + 18} textAnchor="middle" fontSize="11" fill={MUTED}>{fmtAxis(t, model.temporal, span)}</text>);
    const findNearest = evt => {
      const svg = evt.currentTarget.ownerSVGElement || evt.currentTarget;
      const rect = svg.getBoundingClientRect();
      const px = evt.clientX - rect.left;
      const py = evt.clientY - rect.top;
      let best = null;
      let bestD = Infinity;
      for (const p of model.pts) {
        const dx = sx(p.x) - px;
        const d = model.kind === 'line' ? Math.abs(dx) : dx * dx + (sy(p.y) - py) ** 2;
        if (d < bestD) { bestD = d; best = p; }
      }
      if (!best || (model.kind === 'scatter' && bestD > 400)) return setHover(null);
      setHover({ x: sx(best.x), y: sy(best.y), point: true, lines: [`${data.x}: ${formatValue(best.raw.x, model.temporal ? 'Datetime' : '')}`, `${data.y}: ${formatValue(best.y)}`] });
    };
    if (model.kind === 'line') {
      const d = model.pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('');
      marks = <path d={d} fill="none" stroke={MARK} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />;
    } else {
      const r = model.pts.length > 2000 ? 3 : 4;
      marks = model.pts.map((p, i) => <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={r} fill={MARK} stroke={SURFACE} strokeWidth="1.5" opacity={model.pts.length > 500 ? 0.6 : 0.9} />);
    }
    overlay = <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" onMouseMove={findNearest} onMouseLeave={() => setHover(null)} />;
  }

  return (
    <div ref={ref} className="relative w-full">
      <svg width={width} height={height} role="img" aria-label={`${data.type} chart of ${data.y ? `${data.y} by ` : ''}${data.x}`}>
        {yt.map(t => (
          <g key={t}>
            <line x1={m.left} x2={m.left + iw} y1={sy(t)} y2={sy(t)} stroke={GRID} strokeWidth="1" />
            <text x={m.left - 8} y={sy(t) + 4} textAnchor="end" fontSize="11" fill={MUTED}>{compactNumber(t)}</text>
          </g>
        ))}
        <line x1={m.left} x2={m.left + iw} y1={sy(Math.max(y0, 0))} y2={sy(Math.max(y0, 0))} stroke="#d4c4ae" strokeWidth="1" />
        {marks}
        {xAxis}
        {hover?.point && model.kind === 'line' && <line x1={hover.x} x2={hover.x} y1={m.top} y2={m.top + ih} stroke={MUTED} strokeWidth="1" />}
        {hover?.point && <circle cx={hover.x} cy={hover.y} r="5" fill={MARK} stroke={SURFACE} strokeWidth="2" pointerEvents="none" />}
        {overlay}
        {xLabel && <text x={m.left + iw / 2} y={height - 4} textAnchor="middle" fontSize="12" fill={INK}>{xLabel}</text>}
        {yLabel && <text transform={`translate(14,${m.top + ih / 2}) rotate(-90)`} textAnchor="middle" fontSize="12" fill={INK}>{yLabel}</text>}
      </svg>
      {hover && (
        <div className="pointer-events-none absolute z-10 bg-white border border-grid-border rounded-DEFAULT px-2 py-1 text-[12px] shadow-[0_8px_16px_rgba(0,0,0,0.06)] whitespace-nowrap"
          style={{ left: Math.min(hover.x + 12, width - 180), top: Math.max(0, hover.y - 44) }}>
          {hover.lines.map((l, i) => <div key={i} className={i === 0 ? 'text-on-surface-variant' : 'text-on-surface font-medium'}>{l}</div>)}
        </div>
      )}
    </div>
  );
}
