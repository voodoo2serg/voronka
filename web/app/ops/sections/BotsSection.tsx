'use client';

import type { Row } from '../types';

export function BotsSection({
  hub,
  preflight,
  busy,
  onActivate,
  onRefresh,
}: {
  hub: Row[];
  preflight: Row | null;
  busy: boolean;
  onActivate: (id: string) => void;
  onRefresh: () => void;
}) {
  return (
    <div>
      <div className="page-heading">
        <div>
          <h1>Боты</h1>
          <p className="muted">Connection + preflight. Activate webhook без Telegram WebApp.</p>
        </div>
        <button disabled={busy} onClick={onRefresh}>
          Обновить
        </button>
      </div>
      {preflight && (
        <div className="metrics compact-metrics">
          <article>
            <p>Database</p>
            <strong>{preflight.database ? 'ok' : '—'}</strong>
          </article>
          <article>
            <p>Redis</p>
            <strong>{preflight.redis ? 'ok' : '—'}</strong>
          </article>
          <article>
            <p>Worker</p>
            <strong>{preflight.worker_alive ? 'ok' : '—'}</strong>
          </article>
          <article>
            <p>Media</p>
            <strong>{preflight.media_writable ? 'rw' : 'ro'}</strong>
          </article>
        </div>
      )}
      <div className="panel table-panel">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Бот</th>
                <th>Platform</th>
                <th>Role</th>
                <th>Webhook</th>
                <th>video_note</th>
                <th>Last error</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {hub.map((c) => {
                const caps = c.capabilities || {};
                const vn = caps.video_note || {};
                const pf = c.webhook_preflight || {};
                return (
                  <tr key={c.id}>
                    <td>
                      <b>{c.name}</b>
                      <small className="muted mono">{c.id}</small>
                    </td>
                    <td>{c.platform}</td>
                    <td>{c.role || 'bot'}</td>
                    <td>
                      {pf.ok ? (
                        <span className="badge good">ok</span>
                      ) : (
                        <span className="badge">не готов</span>
                      )}
                      <small className="muted">
                        token {c.token_present === false ? 'нет' : c.token_present ? 'есть' : '—'} · secret{' '}
                        {c.secret_present === false ? 'нет' : c.secret_present ? 'есть' : '—'}
                      </small>
                    </td>
                    <td>
                      {vn.available ? (
                        <span className="badge good">native</span>
                      ) : (
                        <span className="badge">{vn.mode || 'n/a'}</span>
                      )}
                      <small className="muted">{vn.note}</small>
                    </td>
                    <td>
                      <small className="danger-text">{c.last_error || '—'}</small>
                      <small className="muted">
                        outbox fail {c.outbox_failed_or_unknown || 0} · pending {c.outbox_pending || 0}
                      </small>
                      <small className="muted">media_writable: {String(c.media_writable)}</small>
                    </td>
                    <td>
                      <button className="primary" disabled={busy || c.platform === 'email'} onClick={() => onActivate(c.id)}>
                        Activate webhook
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!hub.length && <div className="empty">Нет подключений в проекте</div>}
        </div>
      </div>
    </div>
  );
}
