'use client';

import { useState } from 'react';
import type { Row } from '../types';

export function AutomationsSection({
  bots,
  busy,
  onCreate,
}: {
  bots: Row[];
  busy: boolean;
  onCreate: (payload: Row) => Promise<Row>;
}) {
  const [name, setName] = useState('Канал → DM');
  const [channelId, setChannelId] = useState(bots.find((b) => b.role === 'channel')?.id || bots[0]?.id || '');
  const [dmId, setDmId] = useState(bots.find((b) => b.role === 'bot' || !b.role)?.id || bots[0]?.id || '');
  const [waitDays, setWaitDays] = useState(3);
  const [dmText, setDmText] = useState('Здравствуйте! Напишите, если есть вопросы — поможем.');
  const [result, setResult] = useState<Row | null>(null);

  return (
    <div>
      <div className="page-heading">
        <div>
          <h1>Автоматизации</h1>
          <p className="muted">Мастер: вступил в канал → ждать N дней → личное сообщение.</p>
        </div>
      </div>
      <section className="panel">
        <label>
          Название
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Канал / connection
          <select value={channelId} onChange={(e) => setChannelId(e.target.value)} aria-label="Канал">
            {bots.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name} · {b.role || 'bot'} · {b.platform}
              </option>
            ))}
          </select>
        </label>
        <label>
          DM-бот
          <select value={dmId} onChange={(e) => setDmId(e.target.value)} aria-label="DM бот">
            {bots.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name} · {b.platform}
              </option>
            ))}
          </select>
        </label>
        <label>
          Ждать (дней)
          <input
            type="number"
            min={0}
            max={30}
            value={waitDays}
            onChange={(e) => setWaitDays(Number(e.target.value))}
          />
        </label>
        <label>
          Текст личного сообщения
          <textarea rows={4} value={dmText} onChange={(e) => setDmText(e.target.value)} />
        </label>
        <button
          className="primary"
          disabled={busy || !channelId || !dmId || !name || !dmText}
          onClick={() =>
            void onCreate({
              name,
              channel_connection_id: channelId,
              dm_connection_id: dmId,
              wait_days: waitDays,
              dm_text: dmText,
              publish: false,
            }).then(setResult)
          }
        >
          Создать сценарий
        </button>
        {result && (
          <p role="status" className="notice">
            Funnel {result.id}, entry {result.entry_token}, wait {result.wait_seconds}s · DM via{' '}
            {result.dm_connection_id}. {result.note}
          </p>
        )}
      </section>
    </div>
  );
}
