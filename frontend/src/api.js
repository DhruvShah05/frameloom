// All calls go to the same origin. In production the FastAPI server serves this
// app; in `npm run dev` Vite proxies these paths to the backend (vite.config.js).
export const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function parseError(res) {
  try {
    const body = await res.json();
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
    return JSON.stringify(body);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

export async function api(path, { method = 'GET', body, query } = {}) {
  let url = `${API_BASE}${path}`;
  if (query) {
    const qs = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null));
    url += `?${qs}`;
  }
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError(`Can't reach the Loom server (${e.message}). Is it running?`, 0);
  }
  if (!res.ok) throw new ApiError(await parseError(res), res.status);
  return res.json();
}

/** POST and save the response as a file download. */
export async function download(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(await parseError(res), res.status);
  const blob = await res.blob();
  const disposition = res.headers.get('Content-Disposition') || '';
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const filename = match ? decodeURIComponent(match[1]) : 'export';
  saveBlob(blob, filename);
  return filename;
}

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function rowsToCsv(rows, columns) {
  const esc = v => {
    if (v === null || v === undefined) return '';
    const s = Array.isArray(v) ? v.join('; ') : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(esc).join(','), ...rows.map(r => columns.map(c => esc(r[c])).join(','))].join('\n');
}

// ---------------------------------------------------------------- formatting

export const isNumericType = t => /^(U?Int|Float|Decimal)/.test(t || '');
export const isTemporalType = t => /^(Date|Datetime|Time|Duration)/.test(t || '');
export const isDateType = t => /^(Date|Datetime)/.test(t || '');
export const isTextType = t => /^(String|Utf8|Categorical|Enum)/.test(t || '');

export function formatValue(value, type) {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'number') {
    if (Number.isInteger(value)) return Math.abs(value) >= 10000 ? value.toLocaleString() : String(value);
    return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
  }
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string' && isDateType(type)) return value.replace('T', ' ');
  if (Array.isArray(value)) return `[${value.join(', ')}]`;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function compactNumber(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '–';
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, '')}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  if (abs >= 1e4) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}K`;
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toLocaleString(undefined, { maximumFractionDigits: abs < 1 ? 4 : 2 });
}
