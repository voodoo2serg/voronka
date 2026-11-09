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
}: {
  items: Row[];
  busy: boolean;
  onOpen: (id: string) => Promise<void>;
  setForm: (form: FormConfig) => void;
  api: (path: string, method?: string, body?: unknown) => Promise<any>;
  onNotice: (msg: string) => void;
  refresh: () => Promise<void>;
}) {
  return (
    <section className="panel">
      <h2>Диалоги</h2>
      <p className="muted">Только подтверждённые подключения. Импортированные ID без диалога сюда не попадают.</p>
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
                  <small>{item.identities?.length || 0} диалог(ов)</small>
                </span>
              </button>
              <time>{date(last?.created_at)}</time>
            </div>
            <p>{text}</p>
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
