'use client';

import { useState } from 'react';
import type { Row } from '../types';
import { stateNames } from '../constants';

export function BroadcastsSection({
  bots,
  identities,
  assets,
  campaigns,
  busy,
  onCreate,
  onPreflight,
  onLaunch,
}: {
  bots: Row[];
  identities: Row[];
  assets: Row[];
  campaigns: Row[];
  busy: boolean;
  onCreate: (payload: Row) => Promise<void>;
  onPreflight: (id: string) => Promise<void>;
  onLaunch: (id: string) => Promise<void>;
}) {
  const messengerBots = bots.filter((b) => b.platform !== 'email');
  const [connectionId, setConnectionId] = useState(messengerBots[0]?.id || '');
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [assetId, setAssetId] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const audience = identities.filter((i) => !connectionId || i.connection_id === connectionId);
  const circles = assets.filter((a) => a.kind === 'video_note' || a.kind === 'video' || a.kind === 'photo');

  return (
    <div>
      <div className="page-heading">
        <div>
          <h1>Рассылки</h1>
          <p className="muted">Мгновенные мессенджер-blasts. Email newsletter — в CRM.</p>
        </div>
      </div>
      <div className="two-column">
        <section className="panel">
          <h2>Новая рассылка</h2>
          <label>
            Бот
            <select value={connectionId} onChange={(e) => { setConnectionId(e.target.value); setSelected([]); }} aria-label="Бот рассылки">
              {messengerBots.map((b) => (
                <option key={b.id} value={b.id}>{b.name} ({b.platform})</option>
              ))}
            </select>
          </label>
          <label>
            Заголовок
            <input value={title} onChange={(e) => setTitle(e.target.value)} required />
          </label>
          <label>
            Текст
            <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} required />
          </label>
          <label>
            Кружок / медиа (asset_id)
            <select value={assetId} onChange={(e) => setAssetId(e.target.value)} aria-label="Медиа asset">
              <option value="">Только текст</option>
              {circles.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.kind}){a.blocker ? ' — блок' : ''}
                </option>
              ))}
            </select>
          </label>
          <h3>Аудитория (Identity)</h3>
          <div className="preview-scroll">
            {audience.map((i) => (
              <label key={i.id} className="preview-row" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input
                  type="checkbox"
                  checked={selected.includes(i.id)}
                  onChange={(e) =>
                    setSelected((prev) => (e.target.checked ? [...prev, i.id] : prev.filter((x) => x !== i.id)))
                  }
                />
                <span>
                  {i.name} <small className="muted">{i.platform} · {i.external_user_id}</small>
                  {i.stopped && <span className="badge">stopped</span>}
                </span>
              </label>
            ))}
            {!audience.length && <p className="muted">Нет Identity для выбранного бота. Нужен Start / диалог.</p>}
          </div>
          <button
            className="primary"
            disabled={busy || !title || !text || !connectionId || !selected.length}
            onClick={() =>
              void onCreate({
                connection_id: connectionId,
                title,
                text,
                identity_ids: selected,
                asset_id: assetId || null,
              })
            }
          >
            Создать черновик
          </button>
        </section>
        <section className="panel">
          <h2>Кампании</h2>
          {campaigns
            .filter((c) => {
              const bot = bots.find((b) => b.id === c.connection_id);
              return bot && bot.platform !== 'email';
            })
            .map((c) => (
              <div key={c.id} className="campaign">
                <div className="row">
                  <b>{c.title}</b>
                  <span className="badge">{stateNames[c.state] || c.state}</span>
                </div>
                <p>{c.text}</p>
                {c.asset_id && <small className="muted">asset: {c.asset_id}</small>}
                <div className="button-stack">
                  <button disabled={busy || c.state !== 'draft'} onClick={() => void onPreflight(c.id)}>
                    Preflight
                  </button>
                  <button className="primary" disabled={busy || c.state !== 'draft'} onClick={() => void onLaunch(c.id)}>
                    Launch
                  </button>
                </div>
              </div>
            ))}
          {!campaigns.length && <div className="empty compact">Пока нет кампаний</div>}
        </section>
      </div>
    </div>
  );
}
