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

type CartItem = {
  asset_id: string;
  kind: string;
  title: string;
  node_kind: string | null;
  kind_label?: string;
};

type CartState = {
  cart_id?: string | null;
  project_id?: string | null;
  items: CartItem[];
  kinds: string[];
  node_kinds: string[];
  empty: boolean;
  configured?: boolean;
  error?: string | null;
  not_found?: boolean;
  cms_cart_url?: string;
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
  cartId,
  busy,
  onBusy,
  onNotice,
  onOpenConnections,
  onOpenMediaLibrary,
}: {
  api: ApiFn;
  pid: string;
  cartId?: string | null;
  busy: boolean;
  onBusy: (fn: () => Promise<void>) => Promise<void>;
  onNotice: (v: string) => void;
  onOpenConnections?: () => void;
  onOpenMediaLibrary?: () => void;
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
  const [cart, setCart] = useState<CartState | null>(null);
  const [plusOpenAt, setPlusOpenAt] = useState<string | null>(null);

  async function refresh() {
    if (!pid) return;
    const qs = new URLSearchParams({ project_id: pid });
    if (cartId) qs.set('cart_id', cartId);
    const [m, list, cartRow] = await Promise.all([
      api(`/constructor/meta?project_id=${encodeURIComponent(pid)}`),
      api(`/projects/${pid}/action-chains`),
      api(`/constructor/cart?${qs.toString()}`).catch(() => null),
    ]);
    setMeta(m);
    setChains(list.items || []);
    if (cartRow && typeof cartRow === 'object') {
      setCart({
        cart_id: cartRow.cart_id ?? null,
        project_id: cartRow.project_id ?? pid,
        items: Array.isArray(cartRow.items) ? cartRow.items : [],
        kinds: Array.isArray(cartRow.kinds) ? cartRow.kinds : [],
        node_kinds: Array.isArray(cartRow.node_kinds) ? cartRow.node_kinds : [],
        empty: Boolean(cartRow.empty ?? !(cartRow.items || []).length),
        configured: cartRow.configured,
        error: cartRow.error ?? null,
        not_found: cartRow.not_found,
        cms_cart_url: cartRow.cms_cart_url,
      });
    } else {
      setCart({
        items: [],
        kinds: [],
        node_kinds: [],
        empty: true,
        project_id: pid,
      });
    }
  }

  useEffect(() => {
    void onBusy(refresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, cartId]);

  function addFromCartItem(item: CartItem) {
    const nodeKind = item.node_kind || 'contact';
    const label =
      item.title ||
      item.kind_label ||
      ((meta?.node_kinds as Row[] | undefined) || []).find((k) => k.id === nodeKind)?.label ||
      nodeKind;
    setNodes((prev) => [
      ...prev,
      {
        id: uid(),
        kind: nodeKind,
        title: String(label),
        config: {
          cart_kind: item.kind,
          ...(nodeKind === 'payment' ? { provider: 'robokassa', tariff_mode: 'fixed' } : {}),
        },
        media_asset_id: item.asset_id,
        from_address: null,
      },
    ]);
    setSelected(nodes.length);
    setPlusOpenAt(null);
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
  const mediaUrl = String(
    meta?.media_library_url || signalMediaLibraryHref({ project: pid }) || SIGNAL_MEDIA_LIBRARY_URL,
  );
  const cartUrl = String(cart?.cms_cart_url || cmsChainCartHref(pid));
  const cartItems = (cart?.items || []).filter((i) => i.node_kind);
  const cartEmpty = !cartItems.length;
  const needsChannel =
    current && (current.kind === 'email' || current.kind === 'telegram' || current.kind === 'payment');

  function PlusMenu({ menuKey, below }: { menuKey: string; below?: boolean }) {
    const open = plusOpenAt === menuKey;
    if (cartEmpty) {
      return (
        <a
          className={`constructor-plus${below ? ' below' : ''} constructor-plus-cta`}
          href={cartUrl}
          target="_blank"
          rel="noopener noreferrer"
          title="Набрать корзину в CMS"
        >
          +
        </a>
      );
    }
    return (
      <div className={`constructor-plus-wrap${below ? ' is-below' : ''}`}>
        <button
          type="button"
          className={`constructor-plus${below ? ' below' : ''}`}
          title="Добавить шаг из корзины"
          aria-expanded={open}
          aria-haspopup="menu"
          onClick={() => setPlusOpenAt((cur) => (cur === menuKey ? null : menuKey))}
        >
          +
        </button>
        {open && (
          <div className="constructor-plus-menu" role="menu" aria-label="Шаги из корзины">
            {cartItems.map((item) => (
              <button
                key={`${item.asset_id}:${item.kind}`}
                type="button"
                role="menuitem"
                onClick={() => addFromCartItem(item)}
              >
                <b>{item.title || item.kind_label || item.kind}</b>
                <small>
                  {item.kind_label || item.kind} → {item.node_kind}
                </small>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

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

        <div className="constructor-cart-rail" aria-label="Корзина цепочки">
          <h3>Корзина CMS</h3>
          {cartEmpty ? (
            <p className="fine">
              Пусто.{' '}
              <a href={cartUrl} target="_blank" rel="noopener noreferrer">
                Набрать в CMS
              </a>
            </p>
          ) : (
            <ul className="constructor-cart-items">
              {cartItems.map((item) => (
                <li key={`${item.asset_id}:${item.kind}`}>
                  <button type="button" onClick={() => addFromCartItem(item)} disabled={busy}>
                    <b>{item.title || item.kind_label || item.kind}</b>
                    <small>
                      {item.kind} · {item.asset_id.slice(0, 8)}…
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
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
          {onOpenMediaLibrary ? (
            <button type="button" className="primary" onClick={onOpenMediaLibrary}>
              Из медиатеки
            </button>
          ) : (
            <a className="primary" href="?section=media#media">
              Из медиатеки
            </a>
          )}
          <a className="constructor-cart-link" href={cartUrl} target="_blank" rel="noopener noreferrer">
            Набрать корзину в CMS
          </a>
          <a className="constructor-cart-link" href={mediaUrl} target="_blank" rel="noopener noreferrer">
            Открыть в Signal
          </a>
          <p className="fine">
            «+» собирает шаги только из корзины CMS (
            <code>/#/admin/cart</code>
            ). Медиатека — в CRM; Signal — запасной путь. Каналы — в Подключениях.
          </p>
        </div>

        <div className="constructor-flow">
          <div className="constructor-fixed">
            <strong>Клиент получает</strong>
            <p className="fine">Базовый контур коммуникации</p>
          </div>
          <PlusMenu menuKey="flow" />
          {nodes.map((node, index) => (
            <div key={node.id} className={`constructor-square${selected === index ? ' selected' : ''}`}>
              <button
                type="button"
                className="constructor-media"
                title="Из медиатеки"
                aria-label="Из медиатеки"
                onClick={() => {
                  if (onOpenMediaLibrary) onOpenMediaLibrary();
                  else window.open(mediaUrl, '_blank', 'noopener');
                }}
              >
                +
              </button>
              <button type="button" className="square-body" onClick={() => setSelected(index)}>
                <b>{node.title}</b>
                <small>
                  {node.kind}
                  {node.media_asset_id ? ` · ${String(node.media_asset_id).slice(0, 8)}…` : ''}
                </small>
              </button>
              <PlusMenu menuKey={node.id} below />
            </div>
          ))}
        </div>

        {!cartEmpty && (
          <div className="constructor-kinds" role="group" aria-label="Шаги из корзины">
            {cartItems.map((item) => (
              <button
                key={`kind-${item.asset_id}:${item.kind}`}
                type="button"
                onClick={() => addFromCartItem(item)}
                disabled={busy}
              >
                + {item.title || item.kind_label || item.kind}
              </button>
            ))}
          </div>
        )}

        {cartEmpty && (
          <div className="empty compact constructor-empty-cart">
            <p>
              Корзина пуста для этого проекта. Создайте материал в медиатеке CRM, наберите корзину в CMS —
              меню «+» появится по типам из корзины.
            </p>
            <div className="row constructor-cart-ctas-inline">
              {onOpenMediaLibrary ? (
                <button type="button" className="primary" onClick={onOpenMediaLibrary}>
                  Из медиатеки
                </button>
              ) : (
                <a className="primary" href="?section=media#media">
                  Из медиатеки
                </a>
              )}
              <a href={cartUrl} target="_blank" rel="noopener noreferrer">
                Набрать корзину в CMS
              </a>
              <a href={mediaUrl} target="_blank" rel="noopener noreferrer">
                Открыть в Signal
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
              <p className="fine">Тело и медиа — из корзины CMS (asset привязан при добавлении шага).</p>
            )}
            {current.media_asset_id ? (
              <p className="fine">
                Asset из корзины: <code>{current.media_asset_id}</code>
                {current.config.cart_kind ? (
                  <>
                    {' '}
                    · kind <code>{String(current.config.cart_kind)}</code>
                  </>
                ) : null}
              </p>
            ) : (
              <p className="fine">Нет привязанного asset — добавьте шаг из корзины через «+».</p>
            )}
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
