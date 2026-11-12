'use client';

import type { FormConfig, Row } from '../types';
import { date } from '../utils';

export function InboxSection({
  items,
  busy,
  onOpen,
  setForm,
  api,
  onNotice,
  refresh,
  connections = [],
  filters,
  setFilters,
  meId,
}: {
  items: Row[];
  busy: boolean;
  onOpen: (id: string) => Promise<void>;
  setForm: (form: FormConfig) => void;
  api: (path: string, method?: string, body?: unknown) => Promise<any>;
  onNotice: (msg: string) => void;
  refresh: () => Promise<void>;
  connections?: Row[];
  filters: { connection_id: string; unread: boolean; delivery_errors: boolean; mine: boolean };
  setFilters: (next: { connection_id: string; unread: boolean; delivery_errors: boolean; mine: boolean }) => void;
  meId?: number;
}) {
  return (
    <section className="panel">
      <h2>Диалоги</h2>
      <p className="muted">Только подтверждённые подключения. Без Chatwoot/Espo — данные в PostgreSQL voronka.</p>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <select
          value={filters.connection_id}
          onChange={(e) => setFilters({ ...filters, connection_id: e.target.value })}
          aria-label="Фильтр подключения"
        >
          <option value="">Все подключения</option>
          {connections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} · {c.platform}
            </option>
          ))}
        </select>
        <label className="fine">
          <input
            type="checkbox"
            checked={filters.unread}
            onChange={(e) => setFilters({ ...filters, unread: e.target.checked })}
          />{' '}
          Непрочитанные
        </label>
        <label className="fine">
          <input
            type="checkbox"
            checked={filters.delivery_errors}
            onChange={(e) => setFilters({ ...filters, delivery_errors: e.target.checked })}
          />{' '}
          Ошибки Outbox
        </label>
        <label className="fine">
          <input
            type="checkbox"
            checked={filters.mine}
            onChange={(e) => setFilters({ ...filters, mine: e.target.checked })}
          />{' '}
          Мои
        </label>
      </div>
      {items.map((item) => {
        const last = item.last_message;
        const text = last?.payload?.text || last?.payload?.answer || last?.kind || 'Нет сообщений';
        return (
          <article className="campaign" key={item.id}>
            <div className="row">
              <button className="name-cell" onClick={() => void onOpen(item.id)}>
                <span className="avatar">{String(item.name || '?').slice(0, 1)}</span>
                <span>
                  {item.name}
                  <small>
                    {item.identities?.length || 0} диалог(ов)
                    {item.unread ? ' · новое' : ''}
                    {item.delivery_errors ? ' · ошибка доставки' : ''}
                  </small>
                </span>
              </button>
              <time>{date(last?.created_at)}</time>
            </div>
            <p>{text}</p>
            {!!item.delivery?.length && (
              <p className="fine">
                Доставка: {item.delivery.slice(0, 3).map((d: Row) => d.status).join(', ')}
              </p>
            )}
            <div className="row">
              <button
                className="primary"
                disabled={busy || !item.identities?.length}
                onClick={() =>
                  setForm({
                    title: 'Ответ клиенту',
                    fields: [
                      {
                        key: 'identity_id',
                        label: 'Диалог',
                        type: 'select',
                        options: item.identities.map((i: Row) => [i.id, `${i.name} · ${i.connection_id}`]),
                      },
                      { key: 'text', label: 'Сообщение', type: 'textarea', required: true },
                    ],
                    save: async (v) => {
                      await api('/api/messages', 'POST', v);
                      onNotice('Ответ поставлен в очередь. Автоматический сценарий приостановлен.');
                      await refresh();
                    },
                  })
                }
              >
                Ответить
              </button>
              <button onClick={() => void onOpen(item.id)}>Открыть карточку</button>
              {meId != null && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void (async () => {
                      await api('/contacts/' + item.id + '/assign', 'POST', { owner_id: meId });
                      onNotice('Диалог назначен вам');
                      await refresh();
                    })()
                  }
                >
                  Назначить мне
                </button>
              )}
            </div>
          </article>
        );
      })}
      {!items.length && (
        <div className="empty">
          <h2>Пока нет активных диалогов</h2>
          <p>Ответ доступен после реального взаимодействия клиента с подключением.</p>
        </div>
      )}
    </section>
  );
}
