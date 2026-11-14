'use client';

import type { Row } from '../types';

export function MediaSection({ assets, policy }: { assets: Row[]; policy: Row | null }) {
  return (
    <div>
      <div className="page-heading">
        <div>
          <h1>Медиа</h1>
          <p className="muted">Банк материалов и кружки для сценариев / рассылок.</p>
        </div>
      </div>
      {policy && (
        <p className="notice banner">
          Сервис-бот кружков: {policy.decision}. {policy.note}
        </p>
      )}
      <div className="panel table-panel">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Имя</th>
                <th>Kind</th>
                <th>Local</th>
                <th>Blocker</th>
              </tr>
            </thead>
            <tbody>
              {assets.map((a) => (
                <tr key={a.id}>
                  <td>
                    <b>{a.name}</b>
                    <small className="muted mono">{a.id}</small>
                  </td>
                  <td>
                    <span className="tag">{a.kind}</span>
                  </td>
                  <td>{a.has_local_file ? 'да' : 'нет'}</td>
                  <td>
                    <small className="danger-text">{a.blocker || '—'}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!assets.length && <div className="empty">Медиабанк пуст — загрузите через Mini App</div>}
        </div>
      </div>
    </div>
  );
}
