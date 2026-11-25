'use client';

import { useEffect, useState } from 'react';
import {
  SIGNAL_MEDIA_LIBRARY_URL,
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
  busy,
  onBusy,
  onNotice,
  onOpenConnections,
  onOpenMediaLibrary,
}: {
  api: ApiFn;
  pid: string;
  /** @deprecated CRM-R2: CMS cart is no longer a staff path; ignored. */
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
  const [plusOpenAt, setPlusOpenAt] = useState<string | null>(null);

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

  function selectStep(index: number) {
    setSelected(index);
    setEditorOpen(true);
    setPlusOpenAt(null);
  }

  const nodeKinds = ((meta?.node_kinds as Row[] | undefined) || []).filter((k) => k?.id);

  function addNodeKind(kindId: string, atIndex?: number) {
    const kindMeta = nodeKinds.find((k) => String(k.id) === kindId);
    const label = kindMeta?.label ? String(kindMeta.label) : kindId;
    const next: NodeRow = {
      id: uid(),
      kind: kindId,
      title: label,
      config: {
        ...(kindId === 'payment' ? { provider: 'robokassa', tariff_mode: 'fixed' } : {}),
      },
      media_asset_id: null,
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
  const needsChannel =
    current && (current.kind === 'email' || current.kind === 'telegram' || current.kind === 'payment');
  const showEmptyGuide = !nodes.length;
  const previewChannel = channelLabel(current, vaultEmail, vaultMessaging, vaultPayment);
  const previewBody = nodeBody(current);

  function PlusMenu({ menuKey, afterIndex }: { menuKey: string; afterIndex?: number }) {
    const open = plusOpenAt === menuKey;
    return (
      <div className="constructor-plus-wrap">
        <button
          type="button"
          className="constructor-plus"
          title="Добавить шаг"
          aria-expanded={open}
          aria-haspopup="menu"
          onClick={() => setPlusOpenAt((cur) => (cur === menuKey ? null : menuKey))}
        >
          +
        </button>
        {open && (
          <div className="constructor-plus-menu" role="menu" aria-label="Типы шагов">
            {nodeKinds.length ? (
              nodeKinds.map((kind) => (
                <button
                  key={String(kind.id)}
                  type="button"
                  role="menuitem"
                  onClick={() => addNodeKind(String(kind.id), afterIndex)}
                >
                  <b>{String(kind.label || kind.id)}</b>
                  <small>{String(kind.id)}</small>
                </button>
              ))
            ) : (
              <p className="fine" role="menuitem">
                Типы шагов загружаются…
              </p>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="constructor-layout" id="action-constructor">
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

          <div className="constructor-next-steps" role="group" aria-label="Медиатека и подключения">
            <button type="button" className="constructor-step-card" onClick={openMedia}>
              <span className="eyebrow">1</span>
              <b>Медиатека CRM</b>
              <small>Создать файл и подписать kind + Project</small>
            </button>
            {onOpenConnections ? (
              <button type="button" className="constructor-step-card" onClick={onOpenConnections}>
                <span className="eyebrow">2</span>
                <b>Подключения</b>
                <small>Почта From, боты и платежи — только из vault</small>
              </button>
            ) : (
              <a className="constructor-step-card" href="#connections">
                <span className="eyebrow">2</span>
                <b>Подключения</b>
                <small>Почта From, боты и платежи — только из vault</small>
              </a>
            )}
          </div>
          <p className="fine constructor-hint">
            «+» добавляет шаг по типу из списка конструктора. Клик по шагу открывает правую панель. Превью справа
            всегда на месте.{' '}
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

          {showEmptyGuide && (
            <div className="empty compact constructor-empty-guide">
              <h3>С чего начать</h3>
              <p>
                Добавьте шаг кнопкой «+» — типы из списка конструктора (контакт, опрос, письмо, оплата…). Файлы —
                из медиатеки. Каналы настраиваются в Подключениях.
              </p>
              <div className="row constructor-start-ctas-inline">
                <button type="button" className="primary" onClick={openMedia}>
                  Из медиатеки
                </button>
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
                        Тело из asset медиатеки <code>{String(current.media_asset_id).slice(0, 8)}…</code>.
                        Допишите текст в панели справа или оставьте asset как есть.
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
                  <p className="fine">Текст — в поле «Сообщение»; файлы — из медиатеки CRM.</p>
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
