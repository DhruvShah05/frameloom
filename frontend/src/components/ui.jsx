import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { onActivity } from '../api';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Accessible dialog: Escape and the backdrop close it, focus moves inside and
 * stays there (Tab cycles), and returns to where it was on close.
 * Pass `as="form"` with onSubmit for dialogs that submit.
 */
export function Modal({ onClose, labelledBy, children, as = 'div', onSubmit, wide = false }) {
  const ref = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    const el = ref.current;
    const first = el?.querySelector('[autofocus], [data-autofocus]') || el?.querySelector(FOCUSABLE);
    first?.focus();
    const onKey = e => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      } else if (e.key === 'Tab' && el) {
        const items = [...el.querySelectorAll(FOCUSABLE)].filter(n => n.offsetParent !== null);
        if (!items.length) return;
        const [a, z] = [items[0], items[items.length - 1]];
        if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
        else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      previous?.focus?.();
    };
  }, [onClose]);
  const Tag = as;
  return (
    <div className="modal-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <Tag ref={ref} role="dialog" aria-modal="true" aria-labelledby={labelledBy}
        className={`modal-content ${wide ? 'modal-wide' : ''}`}
        onSubmit={onSubmit ? e => { e.preventDefault(); onSubmit(e); } : undefined}>
        {children}
      </Tag>
    </div>
  );
}

// ----------------------------------------------------- confirm / prompt
const DialogContext = createContext(null);

/** `const { confirm, prompt } = useDialogs()` - in-app replacements for window.confirm/prompt. */
export function useDialogs() {
  return useContext(DialogContext);
}

export function DialogProvider({ children }) {
  const [dialog, setDialog] = useState(null);
  const [value, setValue] = useState('');

  const confirm = useCallback((title, message, { confirmLabel = 'Confirm', danger = false } = {}) =>
    new Promise(resolve => setDialog({ kind: 'confirm', title, message, confirmLabel, danger, resolve })), []);
  const prompt = useCallback((title, message, initial = '', { confirmLabel = 'Save', multiline = false, placeholder = '' } = {}) => {
    setValue(initial);
    return new Promise(resolve => setDialog({ kind: 'prompt', title, message, confirmLabel, multiline, placeholder, resolve }));
  }, []);

  const close = result => {
    dialog?.resolve(result);
    setDialog(null);
  };

  return (
    <DialogContext.Provider value={{ confirm, prompt }}>
      {children}
      {dialog && (
        <Modal onClose={() => close(dialog.kind === 'confirm' ? false : null)} labelledBy="app-dialog-title"
          as="form" onSubmit={() => close(dialog.kind === 'confirm' ? true : value)}>
          <h2 id="app-dialog-title" className="font-panel-title font-bold text-lg mb-2 text-on-surface">{dialog.title}</h2>
          {dialog.message && <p className="text-on-surface-variant text-sm mb-4">{dialog.message}</p>}
          {dialog.kind === 'prompt' && (dialog.multiline ? (
            <textarea data-autofocus rows={3} className="input mb-4" placeholder={dialog.placeholder} value={value} onChange={e => setValue(e.target.value)} />
          ) : (
            <input data-autofocus className="input mb-4" placeholder={dialog.placeholder} value={value} onChange={e => setValue(e.target.value)} />
          ))}
          <div className="flex gap-2 justify-end">
            <button type="button" className="btn-secondary" onClick={() => close(dialog.kind === 'confirm' ? false : null)}>Cancel</button>
            <button type="submit" className={dialog.danger ? 'btn btn-danger' : 'btn'} data-autofocus={dialog.kind === 'confirm' ? true : undefined}>{dialog.confirmLabel}</button>
          </div>
        </Modal>
      )}
    </DialogContext.Provider>
  );
}

// ------------------------------------------------------- progress bar
/** A thin bar at the very top while any request is running longer than 150 ms. */
export function ProgressBar() {
  const [active, setActive] = useState(false);
  useEffect(() => {
    let timer = null;
    return onActivity(count => {
      clearTimeout(timer);
      if (count > 0) timer = setTimeout(() => setActive(true), 150);
      else setActive(false);
    });
  }, []);
  return <div className={`progress-bar ${active ? 'is-active' : ''}`} role="progressbar" aria-hidden={!active} aria-label="Working" />;
}

export function Spinner({ size = 14 }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-hidden="true" />;
}
