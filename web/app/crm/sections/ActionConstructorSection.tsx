'use client';

import { useEffect, useState } from 'react';
import {
  SIGNAL_MEDIA_LIBRARY_URL,
  cmsChainCartHref,
  signalMediaLibraryHref,
} from '../portals';
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

/** Empty vault → short CTA to Подключения (no credential forms on #today). */
function VaultEmptyCta({ onOpenConnections }: { onOpenConnections?: () => void }) {
  if (onOpenConnections) {
    return (
      <p className="fine">
        Нет настроенных подключений.{' '}
        <button type="button" className="link" onClick={onOpenConnections}>
          Настроить в Подключениях
        </button>
      </p>
    );
  }
  return (
    <p className="fine">
      Нет настроенных подключений.{' '}
      <a href="?section=connections">Настроить в Подключениях</a>
    </p>
  );
}

export function ActionConstructorSection({
  api,
  pid,
  busy,
  onBusy,
  onNotice,
  onOpenConnections,
}: {
  api: ApiFn;
  pid: string;
  busy: boolean;
  onBusy: (fn: () => Promise<void>) => Promise<void>;
  onNotice: (v: string) => void;
  onOpenConnections?: () => void;
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
  const vaultEmail = (meta?.vault_email as Row[] | undefined) || [];
  const vaultPayment = (meta?.vault_payment as Row[] | undefined) || [];
  const vaultMessaging = (meta?.vault_messaging as Row[] | undefined) || [];
  const kinds = (meta?.node_kinds as Row[] | undefined) || [];
  const mediaUrl = String(
    meta?.media_library_url || signalMediaLibraryHref({ project: pid }) || SIGNAL_MEDIA_LIBRARY_URL,
  );
  const cartUrl = cmsChainCartHref(pid);
  const needsChannel =
    current && (current.kind === 'email' || current.kind === 'telegram' || current.kind === 'payment');

  return (
    <div className="constructor-layout" id="action-constructor" data-cart-route="cms/#/admin/cart">
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

        <div className="constructor-cart-ctas" role="group" aria-label="Корзина и медиатека">
          <a className="primary" href={mediaUrl} target="_blank" rel="noopener noreferrer">
            Создать файл в Signal
          </a>
          <a className="constructor-cart-link" href={cartUrl} target="_blank" rel="noopener noreferrer">
            Набрать корзину в CMS
          </a>
          <p className="fine">
            Файлы — в Signal (kind + Project). Корзина цепочки — в CMS Solo-PR (
            <code>/#/admin/cart</code>). Каналы (SMTP, боты, платежи) — в Подключениях.
          </p>
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
                title="Создать файл в Signal"
                aria-label="Создать файл в Signal"
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
        {!nodes.length && (
          <div className="empty compact constructor-empty-cart">
            <p>Корзина пока пуста для этого проекта. Сначала создайте файл в Signal, затем наберите корзину в CMS.</p>
            <div className="row constructor-cart-ctas-inline">
              <a className="primary" href={mediaUrl} target="_blank" rel="noopener noreferrer">
                Создать файл в Signal
              </a>
              <a href={cartUrl} target="_blank" rel="noopener noreferrer">
                Набрать корзину в CMS
              </a>
            </div>
          </div>
        )}
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
              Пауза перед шагом (мин)
              <input
                type="number"
                min={0}
                value={String(current.config.wait_minutes ?? '')}
                onChange={(e) => {
                  const raw = e.target.value;
                  const wait = raw === '' ? undefined : Number(raw);
                  updateNode(selected, {
                    config: {
                      ...current.config,
                      wait_minutes: wait === undefined || Number.isNaN(wait) ? undefined : wait,
                    },
                  });
                }}
                placeholder="0"
              />
            </label>

            {current.kind === 'email' && (
              <>
                {vaultEmail.length ? (
                  <label>
                    Подключение (From)
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
                ) : (
                  <VaultEmptyCta onOpenConnections={onOpenConnections} />
                )}
              </>
            )}

            {current.kind === 'telegram' && (
              <>
                {vaultMessaging.length ? (
                  <label>
                    Бот / канал
                    <select
                      value={String(current.config.vault_id || '')}
                      onChange={(e) => {
                        const vid = e.target.value;
                        updateNode(selected, {
                          config: { ...current.config, vault_id: vid || null },
                        });
                      }}
                    >
                      <option value="">Выбрать подключение…</option>
                      {vaultMessaging.map((v) => (
                        <option key={String(v.id)} value={String(v.id)}>
                          {String(v.label)} · {String(v.type_label || v.type)}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <VaultEmptyCta onOpenConnections={onOpenConnections} />
                )}
              </>
            )}

            {current.kind === 'payment' && (
              <>
                {vaultPayment.length ? (
                  <label>
                    Платёжное подключение
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
                            provider:
                              provider === 'yookassa'
                                ? 'yookassa'
                                : provider === 'robokassa'
                                  ? 'robokassa'
                                  : current.config.provider,
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
                ) : (
                  <VaultEmptyCta onOpenConnections={onOpenConnections} />
                )}
              </>
            )}

            {needsChannel ? null : (
              <p className="fine">Тело и медиа подтянутся из корзины CMS (CART-04).</p>
            )}
            {current.media_asset_id ? (
              <p className="fine">
                Привязанный asset: <code>{current.media_asset_id}</code>
              </p>
            ) : null}
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
