'use client';

import type { Row } from '../types';

export function ScenariosSection({
  funnels,
  bots,
  selectedBot,
  onSelectBot,
  miniAppHref,
}: {
  funnels: Row[];
  bots: Row[];
  selectedBot: string;
  onSelectBot: (id: string) => void;
  miniAppHref: string;
}) {
  const filtered = selectedBot ? funnels.filter((f) => (f.connection_ids || []).includes(selectedBot)) : funnels;
  return (
    <div>
      <div className="page-heading">
        <div>
          <h1>Сценарии</h1>
          <p className="muted">Список Funnel/Version. Редактор узлов — Mini App (не ломаем).</p>
        </div>
        <a className="primary" href={miniAppHref} target="_blank" rel="noreferrer" style={{ display: 'inline-block', padding: '8px 12px' }}>
          Mini App editor ↗
        </a>
      </div>
      <label>
        Бот
        <select value={selectedBot} onChange={(e) => onSelectBot(e.target.value)} aria-label="Бот для сценариев">
          <option value="">Все боты</option>
          {bots.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name} ({b.platform})
            </option>
          ))}
        </select>
      </label>
      <div className="panel table-panel" style={{ marginTop: 16 }}>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Сценарий</th>
                <th>Ревизия</th>
                <th>Connections</th>
                <th>Published</th>
                <th>Узлы</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((f) => (
                <tr key={f.id}>
                  <td>
                    <b>{f.name}</b>
                    <small className="muted mono">{f.id}</small>
                  </td>
                  <td>{f.revision}</td>
                  <td>
                    {(f.connection_ids || []).map((id: string) => (
                      <span key={id} className="tag">
                        {bots.find((b) => b.id === id)?.name || id.slice(0, 8)}
                      </span>
                    ))}
                  </td>
                  <td>{f.version_id ? <span className="badge good">да</span> : <span className="badge">черновик</span>}</td>
                  <td>{Object.keys(f.graph?.nodes || {}).length}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!filtered.length && <div className="empty">Сценариев пока нет</div>}
        </div>
      </div>
    </div>
  );
}
