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
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `n-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function nodeBody(node: NodeRow | undefined): string {
  if (!node) return '';
  return String(node.config.body || node.config.text || '');
}

function channelLabel(
  node: NodeRow | undefined,
  vaultEmail: Row[],
  vaultMessaging: Row[],
  vaultPayment: Row[],
): string {
  if (!node) return '—';
  const vid = String(node.config.from_vault_id || node.config.vault_id || '');
  if (node.kind === 'email') {
    const row = vaultEmail.find((v) => String(v.id) === vid);
    if (row) {
      const addr = (row.meta as Row)?.from_address;
      return addr ? String(addr) : String(row.label);
    }
    return node.from_address ? String(node.from_address) : 'Email · канал не выбран';
  }
  if (node.kind === 'telegram') {
    const row = vaultMessaging.find((v) => String(v.id) === vid);
    return row ? String(row.label) : 'Telegram · бот не выбран';
  }
  if (node.kind === 'payment') {
    const row = vaultPayment.find((v) => String(v.id) === vid);
    return row ? String(row.label) : 'Оплата · подключение не выбрано';
  }
  return String(node.kind);
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
      <a href="#connections">Настроить в Подключениях</a>
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
  /** B-02/B-03: open CRM media panel (Workspace overlay keeps canvas mounted). */
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
  const [selected, setSelected] = useState<number | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
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

  function selectStep(index: number) {
    setSelected(index);
    setEditorOpen(true);
    setPlusOpenAt(null);
  }

  function addFromCartItem(item: CartItem, atIndex?: number) {
    const nodeKind = item.node_kind || 'contact';
    const label =
      item.title ||
      item.kind_label ||
      ((meta?.node_kinds as Row[] | undefined) || []).find((k) => k.id === nodeKind)?.label ||
      nodeKind;
    const next: NodeRow = {
      id: uid(),
      kind: nodeKind,
      title: String(label),
      config: {
        cart_kind: item.kind,
        ...(nodeKind === 'payment' ? { provider: 'robokassa', tariff_mode: 'fixed' } : {}),
      },
      media_asset_id: item.asset_id,
      from_address: null,
    };
    setNodes((prev) => {
      if (atIndex === undefined) return [...prev, next];
      const insertAt = Math.max(0, Math.min(prev.length, atIndex + 1));
      const copy = [...prev];
      copy.splice(insertAt, 0, next);
      return copy;
    });
    const newIndex =
      atIndex === undefined ? nodes.length : Math.max(0, Math.min(nodes.length, atIndex + 1));
    setSelected(newIndex);
    setEditorOpen(true);
    setPlusOpenAt(null);
  }

  function updateNode(index: number, patch: Partial<NodeRow>) {
    setNodes((prev) => prev.map((n, i) => (i === index ? { ...n, ...patch } : n)));
  }

  function removeNode(index: number) {
    setNodes((prev) => prev.filter((_, i) => i !== index));
    setSelected((cur) => {
      if (cur === null) return null;
      if (cur === index) {
        setEditorOpen(false);
        return null;
      }
      return cur > index ? cur - 1 : cur;
    });
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

  function openMedia() {
    if (onOpenMediaLibrary) {
      onOpenMediaLibrary();
      return;
    }
    window.open(mediaUrl, '_blank', 'noopener');
  }

  const current = selected !== null ? nodes[selected] : undefined;
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
  const showEmptyGuide = cartEmpty || !nodes.length;
  const previewChannel = channelLabel(current, vaultEmail, vaultMessaging, vaultPayment);
  const previewBody = nodeBody(current);

  function PlusMenu({ menuKey, afterIndex }: { menuKey: string; afterIndex?: number }) {
    const open = plusOpenAt === menuKey;
    if (cartEmpty) {
      return (
        <a
          className="constructor-plus constructor-plus-cta"
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
      <div className="constructor-plus-wrap">
        <button
          type="button"
          className="constructor-plus"
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
                onClick={() => addFromCartItem(item, afterIndex)}
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
        <div className="panel-title">
          <div>
            <h2>Цепочки</h2>
            <p className="muted">Черновики и публикации</p>
          </div>
          <button
            type="button"
            className="link"
            onClick={() => {
              setChainId('');
              setRevision(1);
              setName('Новая цепочка');
              setNodes([]);
              setSelected(null);
              setEditorOpen(false);
            }}
          >
            + Новая
          </button>
        </div>
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
                  const loaded = Array.isArray(c.nodes) ? (c.nodes as NodeRow[]) : [];
                  setNodes(loaded);
                  setSelected(loaded.length ? 0 : null);
                  setEditorOpen(Boolean(loaded.length));
                }}
              >
                {String(c.name)}
                <small>{String(c.state)}</small>
              </button>
            </li>
          ))}
          {!chains.length && <li className="muted">Пока нет цепочек — создайте новую.</li>}
        </ul>

        <div className="constructor-cart-rail" aria-label="Корзина цепочки">
          <div className="panel-title constructor-rail-subhead">
            <h3>Корзина CMS</h3>
            <a className="link" href={cartUrl} target="_blank" rel="noopener noreferrer">
              Открыть
            </a>
          </div>
          {cartEmpty ? (
            <p className="fine">
              Пусто. Сначала наберите материалы в CMS — меню «+» появится по типам из корзины.
            </p>
          ) : (
            <ul className="constructor-cart-items">
              {cartItems.map((item) => (
                <li key={`${item.asset_id}:${item.kind}`}>
                  <button type="button" onClick={() => addFromCartItem(item)}>
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

      <div className="constructor-stage">
        <section className="panel constructor-canvas">
          <div className="panel-title">
            <div className="constructor-name-block">
              <h2>Холст</h2>
              <label className="constructor-name-field">
                Название цепочки
                <input value={name} onChange={(e) => setName(e.target.value)} />
              </label>
            </div>
            <div className="constructor-actions">
              <button type="button" disabled={busy} onClick={() => void saveChain()}>
                Сохранить
              </button>
              <button type="button" className="primary" disabled={busy} onClick={() => void publishChain()}>
                Опубликовать
              </button>
            </div>
          </div>

          <div className="constructor-next-steps" role="group" aria-label="Корзина и медиатека">
            <button type="button" className="constructor-step-card" onClick={openMedia}>
              <span className="eyebrow">1</span>
              <b>Медиатека CRM</b>
              <small>Создать файл и подписать kind + Project</small>
            </button>
            <a
              className="constructor-step-card"
              href={cartUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              <span className="eyebrow">2</span>
              <b>Корзина CMS</b>
              <small>Набрать ассеты проекта до сборки цепочки</small>
            </a>
            {onOpenConnections ? (
              <button type="button" className="constructor-step-card" onClick={onOpenConnections}>
                <span className="eyebrow">3</span>
                <b>Подключения</b>
                <small>Почта From, боты и платежи — только из vault</small>
              </button>
            ) : (
              <a className="constructor-step-card" href="#connections">
                <span className="eyebrow">3</span>
                <b>Подключения</b>
                <small>Почта From, боты и платежи — только из vault</small>
              </a>
            )}
          </div>
          <p className="fine constructor-hint">
            «+» собирает шаги только из корзины CMS. Клик по шагу открывает правую панель. Превью справа всегда
            на месте.{' '}
            <a href={mediaUrl} target="_blank" rel="noopener noreferrer">
              Открыть в Signal
            </a>
          </p>

          <div className="constructor-flow constructor-flow-vertical" aria-label="Последовательность шагов">
            <div className="constructor-fixed">
              <strong>Клиент получает</strong>
              <p className="fine">Старт цепочки</p>
            </div>
            <div className="constructor-connector" aria-hidden="true">
              <PlusMenu menuKey="flow-start" afterIndex={-1} />
            </div>
            {nodes.map((node, index) => (
              <div key={node.id} className="constructor-step-block">
                <div
                  className={`constructor-square${selected === index ? ' selected' : ''}`}
                  data-step-index={index}
                >
                  <span className="constructor-step-index" aria-hidden="true">
                    {index + 1}
                  </span>
                  <button
                    type="button"
                    className="constructor-media"
                    title="Открыть медиатеку CRM"
                    aria-label="Открыть медиатеку CRM"
                    onClick={openMedia}
                  >
                    +
                  </button>
                  <button type="button" className="square-body" onClick={() => selectStep(index)}>
                    <b>{node.title}</b>
                    <small>
                      {node.kind}
                      {node.media_asset_id ? ` · ${String(node.media_asset_id).slice(0, 8)}…` : ''}
                    </small>
                  </button>
                </div>
                <div className="constructor-connector" aria-hidden="true">
                  <PlusMenu menuKey={node.id} afterIndex={index} />
                </div>
              </div>
            ))}
          </div>

          {showEmptyGuide && cartEmpty && (
            <div className="empty compact constructor-empty-cart">
              <h3>С чего начать</h3>
              <p>
                Корзина пуста для этого проекта. Создайте файл в медиатеке, наберите корзину в CMS — затем меню
                «+» появится по типам из корзины. Каналы настраиваются в Подключениях.
              </p>
              <div className="row constructor-cart-ctas-inline">
                <button type="button" className="primary" onClick={openMedia}>
                  Из медиатеки
                </button>
                <a href={cartUrl} target="_blank" rel="noopener noreferrer">
                  Набрать корзину в CMS
                </a>
                {onOpenConnections ? (
                  <button type="button" className="link" onClick={onOpenConnections}>
                    Настроить в Подключениях
                  </button>
                ) : (
                  <a href="#connections">Настроить в Подключениях</a>
                )}
              </div>
            </div>
          )}

          <div className="constructor-dryrun">
            <h3>Проверить</h3>
            <p className="fine">Dry-run по сохранённой цепочке — email и/или Telegram получатель.</p>
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

        <aside className="constructor-side" aria-label="Превью и редактор шага">
          <section className="panel constructor-preview" aria-label="Превью шага">
            <div className="panel-title">
              <h2>Превью</h2>
              {current ? <span className="badge">{current.kind}</span> : null}
            </div>
            {!current ? (
              <p className="fine">Выберите шаг на холсте — здесь появится текст и канал.</p>
            ) : (
              <>
                <p className="constructor-preview-channel">
                  <span className="eyebrow">КАНАЛ</span>
                  {previewChannel}
                </p>
                <div className="constructor-preview-frame" data-kind={current.kind}>
                  <header>
                    <b>{current.title || 'Без названия'}</b>
                    <small>
                      шаг {(selected ?? 0) + 1} из {nodes.length}
                      {current.config.wait_minutes
                        ? ` · пауза ${String(current.config.wait_minutes)} мин`
                        : ''}
                    </small>
                  </header>
                  <div className="constructor-preview-body">
                    {previewBody ? (
                      <pre>{previewBody}</pre>
                    ) : current.media_asset_id ? (
                      <p className="fine">
                        Тело из asset корзины <code>{String(current.media_asset_id).slice(0, 8)}…</code>
                        {current.config.cart_kind ? (
                          <>
                            {' '}
                            · <code>{String(current.config.cart_kind)}</code>
                          </>
                        ) : null}
                        . Допишите текст в панели справа или оставьте asset как есть.
                      </p>
                    ) : (
                      <p className="fine">Нет текста — откройте шаг и введите сообщение.</p>
                    )}
                  </div>
                </div>
                {nodes.length > 1 && (
                  <ol className="constructor-preview-chain fine" aria-label="Цепочка">
                    {nodes.map((n, i) => (
                      <li key={n.id} className={i === selected ? 'is-current' : undefined}>
                        {i + 1}. {n.title || n.kind}
                      </li>
                    ))}
                  </ol>
                )}
              </>
            )}
          </section>

          <div
            className={`constructor-editor-drawer${editorOpen && current ? ' is-open' : ''}`}
            role="dialog"
            aria-modal="false"
            aria-label="Редактор шага"
            hidden={!(editorOpen && current)}
          >
            {current && selected !== null && (
              <div className="panel constructor-editor">
                <div className="panel-title">
                  <div>
                    <h2>Шаг {(selected ?? 0) + 1}</h2>
                    <p className="muted">{current.kind}</p>
                  </div>
                  <button
                    type="button"
                    className="link"
                    aria-label="Закрыть редактор"
                    onClick={() => setEditorOpen(false)}
                  >
                    Свернуть
                  </button>
                </div>

                <label>
                  Заголовок
                  <input
                    value={current.title}
                    onChange={(e) => updateNode(selected, { title: e.target.value })}
                  />
                </label>
                <label>
                  Сообщение
                  <textarea
                    rows={6}
                    value={previewBody}
                    onChange={(e) =>
                      updateNode(selected, {
                        config: { ...current.config, body: e.target.value, text: e.target.value },
                      })
                    }
                    placeholder="Текст сообщения без тяжёлой загрузки файлов…"
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

                {!cartEmpty && (
                  <label>
                    Asset из корзины
                    <select
                      value={String(current.media_asset_id || '')}
                      onChange={(e) => {
                        const assetId = e.target.value;
                        const item = cartItems.find((i) => i.asset_id === assetId);
                        updateNode(selected, {
                          media_asset_id: assetId || null,
                          config: {
                            ...current.config,
                            cart_kind: item?.kind || current.config.cart_kind,
                          },
                          ...(item?.node_kind && item.node_kind !== current.kind
                            ? { kind: item.node_kind, title: item.title || current.title }
                            : {}),
                        });
                      }}
                    >
                      <option value="">Без asset…</option>
                      {cartItems.map((item) => (
                        <option key={`${item.asset_id}:${item.kind}`} value={item.asset_id}>
                          {item.title || item.kind_label || item.kind} · {item.kind}
                        </option>
                      ))}
                    </select>
                  </label>
                )}

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
                              {(v.meta as Row)?.from_address
                                ? ` · ${String((v.meta as Row).from_address)}`
                                : ''}
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
                    {vaultMessaging.length > 0 && onOpenConnections && (
                      <p className="fine">
                        Webhook бота:{' '}
                        <button type="button" className="link" onClick={onOpenConnections}>
                          Activate в Подключениях
                        </button>
                      </p>
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
                            const provider = row
                              ? String(row.type)
                              : String(current.config.provider || 'robokassa');
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
                  <p className="fine">Тело и медиа — из корзины CMS или поля «Сообщение» выше.</p>
                )}

                <div className="row constructor-editor-actions">
                  <button type="button" className="link danger-text" onClick={() => removeNode(selected)}>
                    Удалить шаг
                  </button>
                  <button type="button" onClick={openMedia}>
                    Из медиатеки
                  </button>
                </div>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
