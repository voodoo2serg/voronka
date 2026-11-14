'use client';

import { useState } from 'react';
import type { Row } from '../types';

export function AiSection({
  status,
  busy,
  onSave,
}: {
  status: Row | null;
  busy: boolean;
  onSave: (payload: Row) => Promise<void>;
}) {
  const [scope, setScope] = useState('global');
  const [provider, setProvider] = useState('inherit');
  const [model, setModel] = useState('');
  const [rules, setRules] = useState('');

  const providers = [
    ['raga', status?.raga],
    ['openrouter', status?.openrouter],
    ['ollama', status?.ollama],
  ] as const;

  return (
    <div>
      <div className="page-heading">
        <div>
          <h1>AI</h1>
          <p className="muted">Провайдеры и правила. Только draft — без автоответа клиенту.</p>
        </div>
      </div>
      <div className="metrics">
        {providers.map(([id, conf]) => (
          <article key={id}>
            <p>{id}</p>
            <strong>{conf?.configured ? 'ok' : 'не настроено'}</strong>
            <small className="muted">
              {id === 'ollama' && conf?.base_url ? conf.base_url : conf?.default_model || conf?.models_mapped?.join(', ') || '—'}
            </small>
          </article>
        ))}
        <article>
          <p>Автоотправка</p>
          <strong>нет</strong>
          <small className="muted">sent_default: {String(status?.sent_default ?? false)}</small>
        </article>
      </div>
      <section className="panel">
        <h2>Правила</h2>
        <label>
          Scope
          <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="AI scope">
            <option value="global">global</option>
            <option value="project">project</option>
            <option value="connection">connection</option>
          </select>
        </label>
        <label>
          Provider
          <select value={provider} onChange={(e) => setProvider(e.target.value)} aria-label="AI provider">
            <option value="inherit">inherit</option>
            <option value="raga">raga</option>
            <option value="openrouter">openrouter</option>
            <option value="ollama">ollama</option>
          </select>
        </label>
        <label>
          Model
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="опционально" />
        </label>
        <label>
          System rules
          <textarea rows={6} value={rules} onChange={(e) => setRules(e.target.value)} />
        </label>
        <button
          className="primary"
          disabled={busy}
          onClick={() =>
            void onSave({
              scope,
              provider,
              model,
              system_rules: rules,
              active: true,
              project_id: scope === 'project' ? undefined : undefined,
            })
          }
        >
          Сохранить правило
        </button>
        <p className="fine">Сохранение правил — owner. Черновики ассистента не уходят клиенту без approve.</p>
      </section>
      <section className="panel" style={{ marginTop: 20 }}>
        <h2>Текущие правила</h2>
        {(status?.rules || []).map((r: Row) => (
          <div key={r.id} className="mini-card">
            <span className="tag">{r.scope}</span>
            <span className="tag">{r.provider}</span>
            {r.model && <span className="tag">{r.model}</span>}
            <p>{r.system_rules || '—'}</p>
          </div>
        ))}
        {!status?.rules?.length && <p className="muted">Правил пока нет</p>}
      </section>
    </div>
  );
}
