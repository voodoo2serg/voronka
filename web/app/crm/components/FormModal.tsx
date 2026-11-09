'use client';

import type { FormConfig } from '../types';

export function FormModal({
  form,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  form: FormConfig;
  busy: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (data: Record<string, string>) => Promise<void>;
}) {
  return (
    <div className="modal-layer">
      <section role="dialog" aria-modal="true" aria-labelledby="form-title" className="modal">
        <div className="panel-title">
          <h2 id="form-title">{form.title}</h2>
          <button aria-label="Закрыть форму" disabled={busy} onClick={onClose}>✕</button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const data = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>;
            for (const f of form.fields) {
              if (f.type === 'checkbox') data[f.key] = data[f.key] ? 'true' : '';
              else if (!(f.key in data)) data[f.key] = '';
            }
            void onSubmit(data);
          }}
        >
          {form.fields.map((f) => (
            <label key={f.key}>
              {f.label}
              {f.type === 'select' ? (
                <select name={f.key} defaultValue={f.value || f.options?.[0]?.[0]} required={f.required}>
                  {f.options?.map(([key, label]) => (
                    <option key={key} value={key}>{label}</option>
                  ))}
                </select>
              ) : f.type === 'textarea' ? (
                <textarea name={f.key} defaultValue={f.value} required={f.required} rows={4} />
              ) : (
                <input
                  name={f.key}
                  type={f.type || 'text'}
                  step={f.type === 'number' ? '0.01' : undefined}
                  min={f.type === 'number' ? '0' : undefined}
                  defaultValue={f.type === 'checkbox' ? undefined : f.value}
                  defaultChecked={f.type === 'checkbox' && f.value === 'true'}
                  required={f.required}
                />
              )}
            </label>
          ))}
          {error && <p role="alert" className="error">{error}</p>}
          <div className="modal-footer">
            <button type="button" disabled={busy} onClick={onClose}>Отмена</button>
            <button className="primary" disabled={busy}>{busy ? 'Сохранение…' : 'Сохранить'}</button>
          </div>
        </form>
      </section>
    </div>
  );
}
