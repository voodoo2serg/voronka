'use client';

import { useState } from 'react';
import type { Row } from '../types';
import { crmHref } from '../portals';

export function SupportSection({
  bots,
  busy,
  onCreate,
}: {
  bots: Row[];
  busy: boolean;
  onCreate: (payload: Row) => Promise<Row>;
}) {
  const [connectionId, setConnectionId] = useState(bots[0]?.id || '');
  const [name, setName] = useState('Поддержка');
  const [menuText, setMenuText] = useState('Чем помочь?');
  const [branches, setBranches] = useState([
    { label: 'Оплата', reply: 'Оплата проходит через CRM. Если чек не пришёл — напишите менеджеру.', escalate: false },
    { label: 'Доступ', reply: 'Доступ после оплаты. Проверьте кабинет на solo-pr.ru.', escalate: false },
    { label: 'Другое', reply: 'Передаю менеджеру.', escalate: true },
  ]);
  const [result, setResult] = useState<Row | null>(null);

  return (
    <div>
      <div className="page-heading">
        <div>
          <h1>Поддержка</h1>
          <p className="muted">Меню → ветки → эскалация. Inbox менеджера — в CRM.</p>
        </div>
        <a href={`${crmHref()}crm/?section=inbox`} target="_blank" rel="noreferrer">
          Открыть CRM inbox ↗
        </a>
      </div>
      <section className="panel">
        <label>
          Бот
          <select value={connectionId} onChange={(e) => setConnectionId(e.target.value)} aria-label="Бот поддержки">
            {bots.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Название сценария
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Текст меню
          <textarea rows={2} value={menuText} onChange={(e) => setMenuText(e.target.value)} />
        </label>
        <h3>Ветки (до 5)</h3>
        {branches.map((b, i) => (
          <div key={i} className="mini-card">
            <label>
              Кнопка
              <input
                value={b.label}
                onChange={(e) => {
                  const next = [...branches];
                  next[i] = { ...b, label: e.target.value };
                  setBranches(next);
                }}
              />
            </label>
            <label>
              Ответ
              <textarea
                rows={2}
                value={b.reply}
                onChange={(e) => {
                  const next = [...branches];
                  next[i] = { ...b, reply: e.target.value };
                  setBranches(next);
                }}
              />
            </label>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="checkbox"
                checked={b.escalate}
                onChange={(e) => {
                  const next = [...branches];
                  next[i] = { ...b, escalate: e.target.checked };
                  setBranches(next);
                }}
              />
              Эскалация менеджеру (task)
            </label>
          </div>
        ))}
        <div className="row">
          <button
            disabled={busy || branches.length >= 5}
            onClick={() => setBranches([...branches, { label: '', reply: '', escalate: false }])}
          >
            + Ветка
          </button>
          <button
            className="primary"
            disabled={busy || !connectionId || !name}
            onClick={() =>
              void onCreate({
                name,
                connection_id: connectionId,
                menu_text: menuText,
                branches,
                publish: false,
              }).then(setResult)
            }
          >
            Создать пайплайн
          </button>
        </div>
        {result && (
          <p role="status" className="notice">
            Сценарий {result.id}. CRM inbox: {result.crm_inbox_url}. Pause run: POST /api/messages.
          </p>
        )}
      </section>
    </div>
  );
}
