'use client';

import { useEffect, useState } from 'react';
import { SIGNAL_MEDIA_LIBRARY_URL } from '../portals';
import type { ApiFn, Row } from '../types';

type NodeRow = {
  id: string;
  kind: string;
  title: string;
  config: Record<string, unknown>;
  media_asset_id?: string | null;
  from_address?: string | null;
};

function uid() {
  return crypto.randomUUID();
}

export function ActionConstructorSection({
  api,
  pid,
  busy,
  onBusy,
  onNotice,
}: {
  api: ApiFn;
  pid: string;
  busy: boolean;
  onBusy: (fn: () => Promise<void>) => Promise<void>;
  onNotice: (v: string) => void;
}) {
  const [meta, setMeta] = useState<Row | null>(null);
  const [chains, setChains] = useState<Row[]>([]);
  const [chainId, setChainId] = useState('');
  const [revision, setRevision] = useState(1);
  const [name, setName] = useState('Новая цепочка');
  const [nodes, setNodes] = useState<NodeRow[]>([]);
  const [dryEmail, setDryEmail] = useState('');
  const [dryTg, setDryTg] = useState('');
  const [selected, setSelected] = useState(0);

  async function refresh() {
    if (!pid) return;
    const [m, list] = await Promise.all([
      api(`/constructor/meta?project_id=${encodeURIComponent(pid)}`),
      api(`/projects/${pid}/action-chains`),
    ]);
    setMeta(m);
    setChains(list.items || []);
  }

  useEffect(() => {
    void onBusy(refresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  function addNode(kind: string) {
    const label =
      ((meta?.node_kinds as Row[] | undefined) || []).find((k) => k.id === kind)?.label || kind;
    setNodes((prev) => [
      ...prev,
      {
        id: uid(),
        kind,
        title: String(label),
        config: kind === 'payment' ? { provider: 'robokassa', tariff_mode: 'fixed' } : {},
        media_asset_id: null,
        from_address: null,
      },
    ]);
    setSelected(nodes.length);
  }

  function updateNode(index: number, patch: Partial<NodeRow>) {
    setNodes((prev) => prev.map((n, i) => (i === index ? { ...n, ...patch } : n)));
  }

  async function saveChain() {
    await onBusy(async () => {
      if (chainId) {
        const row = await api(`/action-chains/${chainId}`, 'PATCH', {
          name,
          nodes,
          revision,
        });
        setRevision(row.revision);
        setChainId(row.id);
        onNotice('Цепочка сохранена');
      } else {
        const row = await api(`/projects/${pid}/action-chains`, 'POST', { name, nodes });
        setChainId(row.id);
        setRevision(row.revision);
        onNotice('Цепочка создана');
      }
      await refresh();
    });
  }

  async function publishChain() {
    await onBusy(async () => {
      if (!chainId) await saveChain();
      const id = chainId;
      if (!id) return;
      const row = await api(`/action-chains/${id}/publish`, 'POST');
      setRevision(row.revision);
      onNotice('Опубликовано в runtime (ActionChain → Funnel)');
      await refresh();
    });
  }

  async function dryRun() {
    await onBusy(async () => {
      if (!chainId) throw new Error('Сначала сохраните цепочку');
      const r = await api(`/action-chains/${chainId}/dry-run`, 'POST', {
        email: dryEmail,
        telegram: dryTg,
      });
      onNotice(`Проверка запущена · ${r.id?.slice?.(0, 8) || 'ok'}`);
    });
  }

  const current = nodes[selected];
  const fromList = (meta?.from_identities as Row[] | undefined) || [];
  const vaultEmail = (meta?.vault_email as Row[] | undefined) || [];
  const vaultPayment = (meta?.vault_payment as Row[] | undefined) || [];
  const kinds = (meta?.node_kinds as Row[] | undefined) || [];
  const mediaUrl = String(meta?.media_library_url || SIGNAL_MEDIA_LIBRARY_URL);

  return (
    <div className="constructor-layout">
      <aside className="constructor-rail panel">
        <h2>Цепочки проекта</h2>
        <button
          type="button"
          className="link"
          onClick={() => {
            setChainId('');
            setRevision(1);
            setName('Новая цепочка');
            setNodes([]);
            setSelected(0);
          }}
        >
          + Новая
        </button>
        <ul className="constructor-list">
          {chains.map((c) => (
            <li key={String(c.id)}>
              <button
                type="button"
                className={chainId === c.id ? 'active' : undefined}
                onClick={() => {
                  setChainId(String(c.id));
                  setName(String(c.name));
                  setRevision(Number(c.revision) || 1);
                  setNodes(Array.isArray(c.nodes) ? (c.nodes as NodeRow[]) : []);
                  setSelected(0);
                }}
              >
                {String(c.name)}
                <small>{String(c.state)}</small>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section className="panel constructor-canvas">
        <div className="panel-title">
          <label>
            Название
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <div className="constructor-actions">
            <button type="button" disabled={busy} onClick={() => void saveChain()}>
              Сохранить
            </button>
            <button type="button" className="primary" disabled={busy} onClick={() => void publishChain()}>
              Опубликовать
            </button>
          </div>
        </div>
        <div className="constructor-flow">
          <div className="constructor-fixed">
            <strong>Клиент получает</strong>
            <p className="fine">Базовый контур коммуникации</p>
          </div>
          <button type="button" className="constructor-plus" onClick={() => addNode('email')} title="Добавить шаг">
            +
          </button>
          {nodes.map((node, index) => (
            <div key={node.id} className={`constructor-square${selected === index ? ' selected' : ''}`}>
              <button
                type="button"
                className="constructor-media"
                title="Открыть медиатеку"
                aria-label="Открыть медиатеку"
                onClick={() => window.open(mediaUrl, '_blank', 'noopener')}
              >
                +
              </button>
              <button type="button" className="square-body" onClick={() => setSelected(index)}>
                <b>{node.title}</b>
                <small>{node.kind}</small>
              </button>
              <button type="button" className="constructor-plus below" onClick={() => addNode('email')}>
                +
              </button>
            </div>
          ))}
        </div>
        <div className="constructor-kinds">
          {kinds.map((k) => (
            <button key={String(k.id)} type="button" onClick={() => addNode(String(k.id))}>
              + {String(k.label)}
            </button>
          ))}
        </div>
        {current && (
          <div className="constructor-config">
            <h3>Настройки шага</h3>
            <label>
              Заголовок
              <input
                value={current.title}
                onChange={(e) => updateNode(selected, { title: e.target.value })}
              />
            </label>
            <label>
              Текст / тело
              <textarea
                rows={4}
                value={String(current.config.body || current.config.text || '')}
                onChange={(e) =>
                  updateNode(selected, {
                    config: { ...current.config, body: e.target.value },
                  })
                }
              />
            </label>
            {current.kind === 'email' && (
              <>
                <label>
                  Подключение (vault)
                  <select
                    value={String(current.config.from_vault_id || current.config.vault_id || '')}
                    onChange={(e) => {
                      const vid = e.target.value;
                      const row = vaultEmail.find((v) => String(v.id) === vid);
                      const addr = row ? String((row.meta as Row)?.from_address || '') : '';
                      updateNode(selected, {
                        from_address: addr || current.from_address || null,
                        config: {
                          ...current.config,
                          from_vault_id: vid || null,
                          vault_id: vid || null,
                        },
                      });
                    }}
                  >
                    <option value="">Выбрать подключение…</option>
                    {vaultEmail.map((v) => (
                      <option key={String(v.id)} value={String(v.id)}>
                        {String(v.label)}
                        {(v.meta as Row)?.from_address ? ` · ${String((v.meta as Row).from_address)}` : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="fine">
                  <a href="?section=connections">Создать новое в Подключениях</a>
                </p>
                <label>
                  От кого
                  <select
                    value={current.from_address || ''}
                    onChange={(e) => updateNode(selected, { from_address: e.target.value || null })}
                  >
                    <option value="">По умолчанию SMTP</option>
                    {fromList.map((f) => (
                      <option key={String(f.id)} value={String(f.address)}>
                        {String(f.label)}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            )}
            {current.kind === 'payment' && (
              <>
                <label>
                  Подключение (vault)
                  <select
                    value={String(current.config.vault_id || '')}
                    onChange={(e) => {
                      const vid = e.target.value;
                      const row = vaultPayment.find((v) => String(v.id) === vid);
                      const provider = row ? String(row.type) : String(current.config.provider || 'robokassa');
                      updateNode(selected, {
                        config: {
                          ...current.config,
                          vault_id: vid || null,
                          provider: provider === 'yookassa' ? 'yookassa' : provider === 'robokassa' ? 'robokassa' : current.config.provider,
                        },
                      });
                    }}
                  >
                    <option value="">Выбрать подключение…</option>
                    {vaultPayment.map((v) => (
                      <option key={String(v.id)} value={String(v.id)}>
                        {String(v.label)} · {String(v.type_label || v.type)}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="fine">
                  <a href="?section=connections">Создать новое в Подключениях</a>
                </p>
                <label>
                  Провайдер
                  <select
                    value={String(current.config.provider || 'robokassa')}
                    onChange={(e) =>
                      updateNode(selected, {
                        config: { ...current.config, provider: e.target.value },
                      })
                    }
                  >
                    <option value="robokassa">Robokassa</option>
                    <option value="yookassa">ЮKassa</option>
                  </select>
                </label>
                <label>
                  Тариф
                  <select
                    value={String(current.config.tariff_mode || 'fixed')}
                    onChange={(e) =>
                      updateNode(selected, {
                        config: { ...current.config, tariff_mode: e.target.value },
                      })
                    }
                  >
                    <option value="fixed">Фиксированный</option>
                    <option value="open">Открытая сумма</option>
                  </select>
                </label>
              </>
            )}
            <p className="fine">
              <a href={mediaUrl} target="_blank" rel="noopener noreferrer">
                Открыть медиатеку
              </a>{' '}
              (Signal SSO, без повторного пароля)
            </p>
            <label>
              asset_id из медиатеки
              <input
                value={current.media_asset_id || ''}
                onChange={(e) => updateNode(selected, { media_asset_id: e.target.value || null })}
                placeholder="вставьте asset_id после выбора в Signal"
              />
            </label>
          </div>
        )}
        <div className="constructor-dryrun">
          <h3>Проверить</h3>
          <label>
            Email
            <input value={dryEmail} onChange={(e) => setDryEmail(e.target.value)} placeholder="you@example.ru" />
          </label>
          <label>
            Telegram
            <input value={dryTg} onChange={(e) => setDryTg(e.target.value)} placeholder="@username или chat_id" />
          </label>
          <button type="button" disabled={busy} onClick={() => void dryRun()}>
            Проверить
          </button>
        </div>
      </section>
    </div>
  );
}
