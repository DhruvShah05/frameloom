import React, { useEffect, useMemo, useRef, useState } from 'react';

/**
 * Searchable list of operations, grouped, with a one-line description each.
 * Opens on click or ⌘/Ctrl+K; arrows + Enter to choose, Esc to close.
 */
export default function OpPicker({ groups, value, onChange, disabled }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const all = useMemo(() => groups.flatMap(g => g.ops.map(o => ({ ...o, group: g.name }))), [groups]);
  const current = all.find(o => o.type === value);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return all;
    const words = q.split(/\s+/);
    return all.filter(o => words.every(w => `${o.label} ${o.desc || ''} ${o.group} ${o.type}`.toLowerCase().includes(w)));
  }, [all, query]);

  useEffect(() => {
    const onKey = e => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k' && !disabled) {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [disabled]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setCursor(Math.max(0, all.findIndex(o => o.type === value)));
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${cursor}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [cursor, open]);

  const choose = op => {
    onChange(op.type);
    setOpen(false);
  };

  const onKeyDown = e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor(c => Math.min(matches.length - 1, c + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setCursor(c => Math.max(0, c - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (matches[cursor]) choose(matches[cursor]); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setOpen(false); }
  };

  return (
    <div className="relative">
      <button type="button" disabled={disabled} onClick={() => setOpen(o => !o)} aria-haspopup="listbox" aria-expanded={open}
        className="w-full flex items-center gap-2 bg-white border border-grid-border rounded-DEFAULT px-2 py-1.5 text-left cursor-pointer hover:border-primary-container disabled:cursor-default disabled:bg-surface-container-low transition-colors">
        <span className="flex flex-col min-w-0 flex-1">
          <span className="font-ui-label text-ui-label text-on-surface truncate">{current?.label || 'Choose an operation'}</span>
          {current?.desc && <span className="text-[11px] text-on-surface-variant truncate">{current.desc}</span>}
        </span>
        {!disabled && <kbd className="text-[10px] text-on-surface-variant border border-grid-border rounded px-1 shrink-0">⌘K</kbd>}
        {!disabled && <span className="material-symbols-outlined text-[18px] text-on-surface-variant">expand_more</span>}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-0 right-0 top-[calc(100%+4px)] z-50 bg-white border border-grid-border rounded-lg shadow-[0_8px_24px_rgba(0,0,0,0.08)] menu-pop min-w-[260px]">
            <div className="flex items-center gap-2 px-2 border-b border-grid-border">
              <span className="material-symbols-outlined text-[16px] text-on-surface-variant">search</span>
              <input ref={inputRef} value={query} onChange={e => { setQuery(e.target.value); setCursor(0); }} onKeyDown={onKeyDown}
                placeholder="Search operations…" aria-label="Search operations" role="combobox" aria-expanded="true" aria-controls="op-picker-list"
                className="flex-1 h-[34px] border-0 outline-none text-[13px] bg-transparent" />
            </div>
            <ul id="op-picker-list" ref={listRef} role="listbox" className="max-h-[360px] overflow-y-auto grid-scroll py-1 m-0 list-none p-0">
              {matches.map((o, i) => {
                const header = i === 0 || matches[i - 1].group !== o.group ? o.group : null;
                return (
                  <React.Fragment key={o.type}>
                    {header && <li className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wider font-semibold text-on-surface-variant" role="presentation">{header}</li>}
                    <li role="option" aria-selected={i === cursor} data-index={i}
                      onMouseEnter={() => setCursor(i)} onMouseDown={e => { e.preventDefault(); choose(o); }}
                      className={`px-3 py-1.5 cursor-pointer ${i === cursor ? 'bg-highlight-bg' : ''} ${o.type === value ? 'border-l-2 border-primary-container' : 'border-l-2 border-transparent'}`}>
                      <div className="text-[13px] text-on-surface">{o.label}</div>
                      {o.desc && <div className="text-[11px] text-on-surface-variant">{o.desc}</div>}
                    </li>
                  </React.Fragment>
                );
              })}
              {!matches.length && <li className="px-3 py-3 text-[12px] text-on-surface-variant">No operation matches “{query}”. Try the SQL step for anything custom.</li>}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
