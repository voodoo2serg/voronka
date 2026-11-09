'use client';

import { useEffect, useRef, useState } from 'react';
import { createCrmApi } from './api';
import { FormModal } from './components/FormModal';
import { LoginScreen } from './components/LoginScreen';
import { sections, sectionHints, stateNames } from './constants';
import { Icon } from './icons';
import { InboxSection } from './sections/InboxSection';
import type { FormConfig, Row } from './types';
import { date, money, names, toMinor } from './utils';

export default function Workspace() {
  const loadVersion = useRef(0);
  const [token, setToken] = useState('');
  const [ready, setReady] = useState(false);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [me, setMe] = useState<Row | null>(null);
  const [pid, setPid] = useState('');
  const [section, setSection] = useState('overview');
  const [overview, setOverview] = useState<Row>({});
  const [contacts, setContacts] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [inbox, setInbox] = useState<Row[]>([]);
  const [deals, setDeals] = useState<Row[]>([]);
  const [products, setProducts] = useState<Row[]>([]);
  const [orders, setOrders] = useState<Row[]>([]);
  const [tasks, setTasks] = useState<Row[]>([]);
  const [campaigns, setCampaigns] = useState<Row[]>([]);
  const [outreach, setOutreach] = useState<Row[]>([]);
  const [outreachDetail, setOutreachDetail] = useState<Row | null>(null);
  const [entries, setEntries] = useState<Row[]>([]);
  const [promos, setPromos] = useState<Row[]>([]);
  const [staff, setStaff] = useState<Row[]>([]);
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [detail, setDetail] = useState<Row | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<FormConfig | null>(null);
  const [board, setBoard] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [paste, setPaste] = useState('');
  const [preview, setPreview] = useState<Row | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});

  const api = createCrmApi(
    () => token,
    () => {
      setToken('');
      setMe(null);
    },
  );

  useEffect(() => {
    // Migrate legacy sessionStorage tokens once; prefer HttpOnly cookie thereafter.
    const legacy = sessionStorage.getItem('crm-token') || '';
    if (legacy) sessionStorage.removeItem('crm-token');
    setToken(legacy);
    setReady(true);
  }, []);

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка');
    } finally {
      setBusy(false);
    }
  }

  async function load(p = pid) {
    if (!p) return;
    const version = ++loadVersion.current;
    const [o, c, d, pr, t, ord, ca, ob, pc, st, ib, en] = await Promise.all([
      api(`/projects/${p}/overview`),
      api(`/projects/${p}/contacts?q=${encodeURIComponent(query)}&offset=${offset}&limit=100`),
      api(`/projects/${p}/deals`),
      api(`/projects/${p}/products`),
      api(`/projects/${p}/tasks`),
      api(`/projects/${p}/orders`),
      api(`/projects/${p}/campaigns`),
      api(`/projects/${p}/outreach-batches`),
      api(`/projects/${p}/promos`),
      api('/staff'),
      api(`/projects/${p}/inbox`),
      api('/api/entries').catch(() => []),
    ]);
    if (version !== loadVersion.current) return;
    setOverview(o);
    setContacts(c.items);
    setTotal(c.total);
    setDeals(d);
    setProducts(pr);
    setTasks(t);
    setOrders(ord);
    setCampaigns(ca);
    setOutreach(ob);
    setPromos(pc);
    setStaff(st);
    setInbox(ib.items || []);
    setEntries(Array.isArray(en) ? en.filter((e: Row) => e.active !== false) : []);
  }

  useEffect(() => {
    if (!ready) return;
    void act(async () => {
      try {
        const m = await api('/me');
        setMe(m);
        setPid((current) => (m.projects.some((p: Row) => p.id === current) ? current : m.projects[0]?.id || ''));
      } catch {
        setMe(null);
        setToken('');
      } finally {
        setSessionChecked(true);
      }
    });
  }, [ready, token]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (me && pid) {
      setDetail(null);
      setSelected([]);
      setPreview(null);
      setContacts([]);
      setDeals([]);
      setOrders([]);
      setTasks([]);
      setInbox([]);
      void act(() => load());
    }
  }, [pid, me, offset]); // eslint-disable-line react-hooks/exhaustive-deps

  async function showContact(id: string) {
    setDetail(await api('/contacts/' + id));
  }
  const clients = names(contacts);
  const productOptions = names(products.filter((p) => p.active));
  const stages = Object.entries(me?.stages || {}) as [string, string][];
  const kinds = Object.entries(me?.product_kinds || {}) as [string, string][];
  const clientName = (id: string) => contacts.find((c) => c.id === id)?.name || id.slice(0, 8);
  async function refreshDetail() {
    await load();
    if (detail) await showContact(detail.id);
  }

  function contactForm(row?: Row) {
    setForm({
      title: row ? 'Изменить клиента' : 'Новый клиент',
      fields: [
        { key: 'name', label: 'Имя', value: row?.name, required: true },
        { key: 'email', label: 'Email', type: 'email', value: row?.email },
        { key: 'phone', label: 'Телефон', value: row?.phone },
        { key: 'company', label: 'Компания', value: row?.company },
        { key: 'source', label: 'Источник', value: row?.source || 'manual' },
        { key: 'tags', label: 'Теги через запятую', value: row?.tags?.join(', ') },
        { key: 'email_allowed', label: 'Разрешена email-рассылка (основание проверено)', type: 'checkbox', value: row?.extra?.email_allowed ? 'true' : '' },
        { key: 'stopped', label: 'Отказ от сообщений', type: 'checkbox', value: row?.stopped ? 'true' : '' },
      ],
      save: async (v) => {
        const { email_allowed, ...values } = v;
        const data = {
          ...values,
          tags: v.tags.split(',').map((s) => s.trim()).filter(Boolean),
          stopped: v.stopped === 'true',
          extra: { ...(row?.extra || {}), email_allowed: email_allowed === 'true' },
          owner_id: row?.owner_id || null,
        };
        const result = await api(row ? '/contacts/' + row.id : `/projects/${pid}/contacts`, row ? 'PUT' : 'POST', row ? { ...data, revision: row.revision } : data);
        await load();
        await showContact(result.id);
      },
    });
  }

  function productForm(row?: Row) {
    setForm({
      title: row ? 'Изменить продукт' : 'Новый продукт или услуга',
      fields: [
        { key: 'name', label: 'Название', required: true, value: row?.name },
        { key: 'kind', label: 'Вид', type: 'select', options: kinds, value: row?.kind || 'audit' },
        { key: 'description', label: 'Описание и результат', type: 'textarea', value: row?.description },
        { key: 'price', label: 'Стоимость, ₽', type: 'number', value: String((row?.price_minor || 0) / 100) },
        { key: 'active', label: 'Доступен для оформления заказа', type: 'checkbox', value: row?.active ? 'true' : '' },
      ],
      save: async (v) => {
        await api(row ? '/products/' + row.id : `/projects/${pid}/products`, row ? 'PUT' : 'POST', {
          name: v.name,
          kind: v.kind,
          description: v.description,
          price_minor: toMinor(v.price),
          active: v.active === 'true',
          config: row?.config || {},
        });
        await load();
      },
    });
  }

  function dealForm(profile?: Row) {
    setForm({
      title: 'Новая сделка',
      fields: [
        { key: 'profile_id', label: 'Клиент', type: 'select', options: clients, value: profile?.id, required: true },
        { key: 'title', label: 'Задача клиента', required: true },
        { key: 'product_id', label: 'Продукт', type: 'select', options: [['', 'Без продукта'], ...names(products)] },
        { key: 'amount', label: 'Ожидаемая сумма, ₽', type: 'number', value: '0' },
        { key: 'next_action', label: 'Следующее действие' },
        { key: 'due_at', label: 'Когда', type: 'datetime-local' },
      ],
      save: async (v) => {
        await api(`/projects/${pid}/deals`, 'POST', {
          profile_id: v.profile_id,
          title: v.title,
          product_id: v.product_id || null,
          amount_minor: toMinor(v.amount),
          next_action: v.next_action,
          due_at: v.due_at ? new Date(v.due_at).toISOString() : null,
        });
        await refreshDetail();
      },
    });
  }

  function taskForm(profile?: Row) {
    setForm({
      title: 'Задача, сессия или результат работы',
      fields: [
        { key: 'profile_id', label: 'Клиент', type: 'select', options: clients, value: profile?.id, required: true },
        { key: 'kind', label: 'Вид работы', type: 'select', options: [['task', 'Задача'], ['session', 'Сессия'], ['deliverable', 'Результат / этап работы'], ['document', 'Документ'], ['reminder', 'Напоминание']] },
        { key: 'title', label: 'Что сделать', required: true },
        { key: 'due_at', label: 'Срок', type: 'datetime-local' },
      ],
      save: async (v) => {
        await api(`/contacts/${v.profile_id}/tasks`, 'POST', {
          title: v.title,
          kind: v.kind,
          due_at: v.due_at ? new Date(v.due_at).toISOString() : null,
        });
        await refreshDetail();
      },
    });
  }

  function orderForm(profile?: Row) {
    const requestKey = crypto.randomUUID();
    setForm({
      title: 'Оформить заказ',
      fields: [
        { key: 'profile_id', label: 'Клиент', type: 'select', options: clients, value: profile?.id, required: true },
        { key: 'product_id', label: 'Продукт', type: 'select', options: productOptions, required: true },
        { key: 'promo_code', label: 'Промокод' },
        ...(me?.role === 'owner'
          ? [
              { key: 'agreed_price', label: 'Индивидуальная цена, ₽ (пусто — по каталогу)', type: 'number' },
              { key: 'price_reason', label: 'Основание индивидуальной цены' },
            ]
          : []),
        { key: 'installments', label: 'График: дата; сумма ₽ — одна строка на взнос', type: 'textarea' },
      ],
      save: async (v) => {
        const installments = v.installments.trim()
          ? v.installments.trim().split('\n').map((line) => {
              const [day, amount] = line.split(';');
              if (!day || !amount) throw new Error('Формат взноса: 2026-12-01; 5000');
              return { due_at: new Date(day.trim() + 'T12:00:00').toISOString(), amount_minor: toMinor(amount.trim()) };
            })
          : [];
        await api(`/projects/${pid}/orders`, 'POST', {
          profile_id: v.profile_id,
          product_id: v.product_id,
          promo_code: v.promo_code,
          request_key: requestKey,
          installments,
          agreed_price_minor: v.agreed_price ? toMinor(v.agreed_price) : null,
          price_reason: v.price_reason || '',
        });
        await refreshDetail();
      },
    });
  }

  function paymentForm(o: Row, refund = false) {
    const key = crypto.randomUUID();
    setForm({
      title: refund ? 'Подтвердить возврат' : 'Подтвердить поступление',
      fields: [
        { key: 'amount', label: 'Сумма, ₽', type: 'number', value: String((refund ? o.paid_minor : o.balance_minor) / 100), required: true },
        { key: 'evidence', label: 'Основание: выписка, номер документа', required: true },
      ],
      save: async (v) => {
        await api(`/orders/${o.id}/payments`, 'POST', {
          amount_minor: toMinor(v.amount),
          external_id: key,
          kind: refund ? 'refund' : 'payment',
          evidence: v.evidence,
        });
        await refreshDetail();
      },
    });
  }

  function grantForm(o: Row) {
    setForm({
      title: 'Назначить право',
      fields: [
        { key: 'resource', label: 'Ключ ресурса / раздела', required: true },
        {
          key: 'capability',
          label: 'Действие',
          type: 'select',
          options: [
            ['course.enter', 'Вход в курс'],
            ['video.play', 'Видео'],
            ['document.view', 'Документ'],
            ['archive.view', 'Архив'],
            ['live.join', 'Онлайн-занятие'],
            ['certificate.issue', 'Сертификат'],
          ],
        },
        { key: 'min_paid_percent', label: 'Минимальная оплата, %', type: 'number', value: '100' },
        { key: 'expires_at', label: 'До даты', type: 'datetime-local' },
      ],
      save: async (v) => {
        await api(`/orders/${o.id}/grants`, 'POST', {
          ...v,
          min_paid_percent: Number(v.min_paid_percent),
          expires_at: v.expires_at ? new Date(v.expires_at).toISOString() : null,
        });
        setNotice('Назначение сохранено. Подключение LMS выполняется отдельно.');
      },
    });
  }

  async function previewFile(fields: Record<string, string> = mapping) {
    const f = file || (paste ? new File([paste], 'contacts.csv', { type: 'text/csv' }) : null);
    if (!f) throw new Error('Выберите CSV или вставьте строки с заголовками');
    const data = new FormData();
    data.append('file', f);
    data.append('mapping', JSON.stringify(fields));
    const p = await api(`/projects/${pid}/imports/preview`, 'POST', data);
    setPreview(p);
    setMapping(p.mapping);
  }

  function campaignForm() {
    setForm({
      title: 'Приветственная кампания',
      fields: [
        { key: 'title', label: 'Название', required: true },
        {
          key: 'connection_id',
          label: 'Подключение',
          type: 'select',
          options: (overview.connections || []).map((c: Row) => [c.id, `${c.name} · ${c.platform}`]),
          required: true,
        },
        { key: 'text', label: 'Сообщение, {name} — имя клиента', type: 'textarea', required: true },
      ],
      save: async (v) => {
        const c = await api(`/projects/${pid}/campaigns`, 'POST', { ...v, profile_ids: selected });
        setNotice(
          `Черновик создан. Доступных диалогов: ${c.report.recipients.filter((r: Row) => r.eligible).length} из ${selected.length}. Импортированные ID не активируются автоматически.`,
        );
        setSection('campaigns');
        await load();
      },
    });
  }

  function outreachForm() {
    const bots = (overview.connections || []).filter((c: Row) => ['telegram', 'max'].includes(c.platform));
    const entryOptions: [string, string][] = entries
      .filter((e: Row) => bots.some((c: Row) => c.id === e.connection_id))
      .map((e: Row) => {
        const conn = bots.find((c: Row) => c.id === e.connection_id);
        return [String(e.token), `${conn?.name || e.connection_id} · ${String(e.token).slice(0, 10)}…`];
      });
    setForm({
      title: 'Партия прогрева (dry_run)',
      fields: [
        { key: 'title', label: 'Название партии', required: true },
        {
          key: 'entry_token',
          label: 'Entry официального бота',
          type: 'select',
          options: entryOptions,
          required: true,
        },
        {
          key: 'daily_cap',
          label: 'Дневной лимит (1–50)',
          type: 'number',
          value: '30',
          required: true,
        },
        {
          key: 'invite_text',
          label: 'Текст: {name}, {link}, {phone}',
          type: 'textarea',
          value: 'Здравствуйте, {name}! Напишите официальному боту: {link}',
          required: true,
        },
      ],
      save: async (v) => {
        const entry = entries.find((e: Row) => e.token === v.entry_token);
        if (!entry) throw new Error('Сначала создайте Entry в редакторе воронок');
        const batch = await api(`/projects/${pid}/outreach-batches`, 'POST', {
          title: v.title,
          invite_text: v.invite_text,
          profile_ids: selected,
          entry_token: v.entry_token,
          entry_connection_id: entry.connection_id,
          daily_cap: Number(v.daily_cap) || 30,
          mode: 'dry_run',
          transport: 'manual',
        });
        const eligible = (batch.report?.preview || []).filter((r: Row) => r.eligible).length;
        setNotice(
          `Партия прогрева создана. С телефоном и без отказа: ${eligible} из ${selected.length}. Dry_run не пишет в Outbox и не шлёт через PyMax.`,
        );
        setSection('outreach');
        setSelected([]);
        await load();
      },
    });
  }

  if (!ready || !sessionChecked) return <main className="loading">Загрузка рабочего кабинета…</main>;
  if (!me)
    return (
      <LoginScreen
        api={api}
        busy={busy}
        error={error}
        notice={notice}
        onBusy={act}
        onError={setError}
        onNotice={setNotice}
        onToken={setToken}
      />
    );

  return (
    <div className="workspace">
      <aside className="sidebar">
        <div className="wordmark">
          <span className="brandmark small">С</span>
          <div>
            Соло-PR<small>Рабочее пространство</small>
          </div>
        </div>
        <label className="project-label">
          ПРОЕКТ
          <select
            aria-label="Проект"
            value={pid}
            onChange={(e) => {
              setOffset(0);
              setPid(e.target.value);
            }}
          >
            {me.projects.map((p: Row) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <nav>
          {sections.map(([id, label]) => (
            <button
              key={id}
              className={section === id ? 'nav active' : 'nav'}
              onClick={() => {
                setSection(id);
                setDetail(null);
                setError('');
              }}
            >
              <span className="nav-icon">
                <Icon name={id} />
              </span>
              {label}
              {id === 'contacts' && <small>{total}</small>}
              {id === 'inbox' && <small>{inbox.length}</small>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <a href="/" target="_blank" rel="noreferrer">
            Открыть редактор воронок ↗
          </a>
          <div className="user">
            <span className="avatar">{me.role === 'owner' ? 'С' : 'М'}</span>
            <div>
              {me.role === 'owner' ? 'Владелец' : me.role === 'editor' ? 'Партнёр' : 'Менеджер'}
              <small>Личный аккаунт</small>
            </div>
            <button
              title="Выйти"
              onClick={() =>
                void act(async () => {
                  await api('/logout', 'POST');
                  sessionStorage.removeItem('crm-token');
                  setToken('');
                  setMe(null);
                })
              }
            >
              ↪
            </button>
          </div>
        </div>
      </aside>
      <div className="main-area">
        <header className="topbar">
          <span>
            Рабочее пространство <b>/</b> {sections.find((s) => s[0] === section)?.[1]}
          </span>
          <button disabled={busy} onClick={() => void act(() => load())}>
            {busy ? 'Обновление…' : 'Обновить'}
          </button>
        </header>
        <main className="content">
          <div className="page-heading">
            <div>
              <p className="eyebrow">СОЛО-PR</p>
              <h1>{sections.find((s) => s[0] === section)?.[1]}</h1>
              <p className="muted">{sectionHints[section]}</p>
            </div>
            {section === 'contacts' && (
              <button className="primary" onClick={() => contactForm()}>
                + Новый клиент
              </button>
            )}
            {section === 'deals' && (
              <button className="primary" disabled={!contacts.length} onClick={() => dealForm()}>
                + Сделка
              </button>
            )}
            {section === 'products' && me.role === 'owner' && (
              <button className="primary" onClick={() => productForm()}>
                + Продукт
              </button>
            )}
            {section === 'tasks' && (
              <button className="primary" disabled={!contacts.length} onClick={() => taskForm()}>
                + Работа
              </button>
            )}
            {section === 'orders' && (
              <button className="primary" disabled={!productOptions.length || !contacts.length} onClick={() => orderForm()}>
                + Заказ
              </button>
            )}
          </div>
          {error && (
            <div role="alert" className="error banner">
              {error}
            </div>
          )}
          {notice && (
            <div role="status" className="notice banner">
              {notice}
            </div>
          )}
          {!pid ? (
            <section className="empty">
              <h2>Начните с проекта</h2>
              <p>Объедините клиентов, продукты и подключения в одном проекте.</p>
              {me.role === 'owner' && (
                <button
                  className="primary"
                  onClick={() =>
                    setForm({
                      title: 'Новый проект',
                      fields: [
                        { key: 'name', label: 'Название', required: true },
                        { key: 'goal', label: 'Задача проекта' },
                      ],
                      save: async (v) => {
                        const p = await api('/api/projects', 'POST', v);
                        const m = await api('/me');
                        setMe(m);
                        setPid(p.id);
                      },
                    })
                  }
                >
                  Создать проект
                </button>
              )}
            </section>
          ) : (
            <>
              {section === 'overview' && (
                <>
                  <div className="metrics">
                    {[
                      ['Клиенты', String(overview.contacts || 0)],
                      ['Сделки', String(overview.deals || 0)],
                      ['В работе', money(overview.pipeline_minor)],
                      ['Получено, за вычетом возвратов', money(overview.received_minor)],
                    ].map(([label, value]) => (
                      <article key={label}>
                        <p>{label}</p>
                        <strong>{value}</strong>
                      </article>
                    ))}
                  </div>
                  <div className="two-column">
                    <section className="panel">
                      <div className="panel-title">
                        <h2>Следующие действия</h2>
                        <button onClick={() => setSection('tasks')}>Все задачи →</button>
                      </div>
                      {tasks
                        .filter((t) => t.state === 'open')
                        .slice(0, 6)
                        .map((t) => (
                          <button className="action-row" key={t.id} onClick={() => void act(() => showContact(t.profile_id))}>
                            <span className="task-dot" />
                            <div>
                              <b>{t.title}</b>
                              <small>{clientName(t.profile_id)}</small>
                            </div>
                            <time>{date(t.due_at)}</time>
                          </button>
                        ))}
                      {!tasks.some((t) => t.state === 'open') && (
                        <div className="empty compact">Нет открытых задач. Назначьте следующий шаг в карточке клиента.</div>
                      )}
                    </section>
                    <section className="panel">
                      <h2>Ваши направления</h2>
                      {kinds.slice(0, 6).map(([kind, label]) => (
                        <div key={kind} className="direction">
                          <span>◇</span>
                          <b>{label}</b>
                          <small>{products.filter((p) => p.kind === kind).length} в каталоге</small>
                        </div>
                      ))}
                      <button className="link" onClick={() => setSection('products')}>
                        Перейти к продуктам →
                      </button>
                    </section>
                  </div>
                </>
              )}
              {section === 'inbox' && (
                <InboxSection
                  items={inbox}
                  busy={busy}
                  onOpen={(id) => act(() => showContact(id))}
                  setForm={setForm}
                  api={api}
                  onNotice={setNotice}
                  refresh={async () => {
                    await load();
                  }}
                />
              )}
              {section === 'contacts' && (
                <section className="panel table-panel">
                  <div className="toolbar">
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        setOffset(0);
                        void act(() => load());
                      }}
                    >
                      <input aria-label="Поиск клиентов" placeholder="Поиск по имени, почте, компании…" value={query} onChange={(e) => setQuery(e.target.value)} />
                      <button>Найти</button>
                    </form>
                    <span className="spacer" />
                    <button
                      onClick={() =>
                        void act(async () => {
                          await api(`/projects/${pid}/sync-contacts`, 'POST');
                          await load();
                        })
                      }
                    >
                      Из ботов
                    </button>
                    <button onClick={() => setSection('imports')}>Импорт</button>
                    <button
                      onClick={() =>
                        void act(async () => {
                          const blob = await api(`/projects/${pid}/export?q=${encodeURIComponent(query)}`, 'GET', undefined, true);
                          const url = URL.createObjectURL(blob);
                          const a = document.createElement('a');
                          a.href = url;
                          a.download = 'clients.csv';
                          a.click();
                          URL.revokeObjectURL(url);
                        })
                      }
                    >
                      Экспорт CSV
                    </button>
                  </div>
                  {selected.length > 0 && (
                    <div className="selection">
                      Выбрано: {selected.length}
                      <button onClick={campaignForm}>Приветственная кампания</button>
                      <button onClick={outreachForm}>Прогрев (dry_run)</button>
                      <button onClick={() => setSelected([])}>Снять выбор</button>
                    </div>
                  )}
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>
                            <input
                              aria-label="Выбрать всех клиентов на странице"
                              type="checkbox"
                              checked={contacts.length > 0 && selected.length === contacts.length}
                              onChange={(e) => setSelected(e.target.checked ? contacts.map((c) => c.id) : [])}
                            />
                          </th>
                          <th>Клиент</th>
                          <th>Компания</th>
                          <th>Контакты</th>
                          <th>Последний результат</th>
                          <th>Источник</th>
                          <th>Теги</th>
                        </tr>
                      </thead>
                      <tbody>
                        {contacts.map((c) => (
                          <tr key={c.id}>
                            <td>
                              <input
                                aria-label={'Выбрать ' + c.name}
                                type="checkbox"
                                checked={selected.includes(c.id)}
                                onChange={(e) => setSelected(e.target.checked ? [...selected, c.id] : selected.filter((i) => i !== c.id))}
                              />
                            </td>
                            <td>
                              <button className="name-cell" onClick={() => void act(() => showContact(c.id))}>
                                <span className="avatar">{c.name.slice(0, 1)}</span>
                                <span>
                                  {c.name}
                                  {c.stopped && <small>Отказ от сообщений</small>}
                                </span>
                              </button>
                            </td>
                            <td>{c.company || '—'}</td>
                            <td>
                              <div>{c.email || '—'}</div>
                              <small>{c.phone}</small>
                            </td>
                            <td>
                              {c.last_contact?.outcome || 'Ещё нет контакта'}
                              <small>{date(c.last_contact?.created_at)}</small>
                            </td>
                            <td>{c.source}</td>
                            <td>
                              {c.tags.map((t: string) => (
                                <span key={t} className="tag">
                                  {t}
                                </span>
                              ))}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {!contacts.length && (
                    <div className="empty">
                      <h2>Здесь появятся ваши клиенты</h2>
                      <p>Добавьте первого вручную, загрузите CSV или перенесите контакты из подключённых ботов.</p>
                    </div>
                  )}
                  <footer className="table-footer">
                    {total} клиентов · показано {contacts.length}
                    <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>
                      ←
                    </button>
                    <button disabled={offset + 100 >= total} onClick={() => setOffset(offset + 100)}>
                      →
                    </button>
                  </footer>
                </section>
              )}
              {section === 'deals' && (
                <>
                  <div className="view-toggle">
                    <button className={board ? 'selected' : ''} onClick={() => setBoard(true)}>
                      Доска
                    </button>
                    <button className={!board ? 'selected' : ''} onClick={() => setBoard(false)}>
                      Таблица
                    </button>
                  </div>
                  {board ? (
                    <div className="board">
                      {stages.map(([key, label]) => (
                        <section className="board-column" key={key}>
                          <h3>
                            <span className={'stage-dot ' + key} />
                            {label}
                            <small>{deals.filter((d) => d.stage === key).length}</small>
                          </h3>
                          {deals
                            .filter((d) => d.stage === key)
                            .map((d) => (
                              <article className="deal-card" key={d.id}>
                                <button className="card-title" onClick={() => void act(() => showContact(d.profile_id))}>
                                  {d.title}
                                </button>
                                <p>{clientName(d.profile_id)}</p>
                                <strong>{money(d.amount_minor)}</strong>
                                <button
                                  className="link"
                                  onClick={() =>
                                    setForm({
                                      title: 'Следующее действие',
                                      fields: [
                                        { key: 'next_action', label: 'Что сделать', value: d.next_action },
                                        { key: 'due_at', label: 'Когда', type: 'datetime-local' },
                                      ],
                                      save: async (v) => {
                                        await api('/deals/' + d.id, 'PATCH', {
                                          stage: d.stage,
                                          revision: d.revision,
                                          next_action: v.next_action,
                                          due_at: v.due_at ? new Date(v.due_at).toISOString() : null,
                                        });
                                        await load();
                                      },
                                    })
                                  }
                                >
                                  Назначить шаг
                                </button>
                                <p className="next-action">{d.next_action || 'Следующий шаг не назначен'}</p>
                                <time>{date(d.due_at)}</time>
                                <select
                                  aria-label={'Этап сделки ' + d.title}
                                  value={d.stage}
                                  onChange={(e) =>
                                    void act(async () => {
                                      await api('/deals/' + d.id, 'PATCH', {
                                        stage: e.target.value,
                                        revision: d.revision,
                                        next_action: d.next_action,
                                        due_at: d.due_at,
                                      });
                                      await load();
                                    })
                                  }
                                >
                                  {stages.map(([s, l]) => (
                                    <option key={s} value={s}>
                                      {l}
                                    </option>
                                  ))}
                                </select>
                              </article>
                            ))}
                        </section>
                      ))}
                    </div>
                  ) : (
                    <div className="panel table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Сделка</th>
                            <th>Клиент</th>
                            <th>Этап</th>
                            <th>Сумма</th>
                            <th>Следующий шаг</th>
                          </tr>
                        </thead>
                        <tbody>
                          {deals.map((d) => (
                            <tr key={d.id}>
                              <td>
                                <button className="link" onClick={() => void act(() => showContact(d.profile_id))}>
                                  {d.title}
                                </button>
                              </td>
                              <td>{clientName(d.profile_id)}</td>
                              <td>{me.stages[d.stage]}</td>
                              <td>{money(d.amount_minor)}</td>
                              <td>{d.next_action}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
              {section === 'products' && (
                <>
                  <div className="toolbar">
                    <span className="muted">Стоимость можно согласовать отдельно для каждого предложения.</span>
                    <span className="spacer" />
                    {me.role === 'owner' && (
                      <button
                        onClick={() =>
                          void act(async () => {
                            await api(`/projects/${pid}/catalog-template`, 'POST');
                            await load();
                          })
                        }
                      >
                        Добавить направления Соло-PR
                      </button>
                    )}
                  </div>
                  <div className="product-grid">
                    {products.map((p) => (
                      <article className="panel product-card" key={p.id}>
                        <div className="row">
                          <span className="tag">{me.product_kinds[p.kind]}</span>
                          <span className={p.active ? 'badge good' : 'badge'}>{p.active ? 'Активен' : 'Черновик'}</span>
                        </div>
                        <h2>{p.name}</h2>
                        <p className="muted">{p.description || 'Описание пока не добавлено'}</p>
                        <div className="row">
                          <strong>{money(p.price_minor)}</strong>
                          <button disabled={me.role !== 'owner'} onClick={() => productForm(p)}>
                            Изменить
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                  {!products.length && <div className="empty">Создайте продукт или добавьте шаблоны направлений. Цены не заполняются вымышленными значениями.</div>}
                </>
              )}
              {section === 'tasks' && (
                <section className="panel table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Работа</th>
                        <th>Клиент</th>
                        <th>Вид</th>
                        <th>Срок</th>
                        <th>Результат</th>
                        <th>Состояние</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tasks.map((t) => (
                        <tr key={t.id}>
                          <td>{t.title}</td>
                          <td>
                            <button className="link" onClick={() => void act(() => showContact(t.profile_id))}>
                              {clientName(t.profile_id)}
                            </button>
                          </td>
                          <td>{t.kind}</td>
                          <td>{date(t.due_at)}</td>
                          <td>{t.result || '—'}</td>
                          <td>
                            <button
                              onClick={() =>
                                setForm({
                                  title: 'Результат работы',
                                  fields: [
                                    {
                                      key: 'state',
                                      label: 'Состояние',
                                      type: 'select',
                                      options: [
                                        ['open', 'В работе'],
                                        ['done', 'Готово'],
                                        ['cancelled', 'Отменено'],
                                      ],
                                      value: t.state,
                                    },
                                    { key: 'result', label: 'Что сделано / ссылка на документ', type: 'textarea', value: t.result },
                                  ],
                                  save: async (v) => {
                                    await api('/tasks/' + t.id, 'PATCH', v);
                                    await load();
                                  },
                                })
                              }
                            >
                              {stateNames[t.state] || t.state}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!tasks.length && <div className="empty">Задач пока нет</div>}
                </section>
              )}
              {section === 'orders' && (
                <>
                  <section className="panel table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Заказ</th>
                          <th>Клиент</th>
                          <th>Итого</th>
                          <th>Оплачено</th>
                          <th>Остаток / просрочка</th>
                          <th>Действия</th>
                        </tr>
                      </thead>
                      <tbody>
                        {orders.map((o) => (
                          <tr key={o.id}>
                            <td>
                              <b>{o.snapshot.name}</b>
                              <small>
                                {o.id.slice(0, 8)} · {stateNames[o.state]}
                                {o.snapshot.promo ? ' · ' + o.snapshot.promo : ''}
                              </small>
                              {o.snapshot.discount_minor > 0 && <small>Скидка {money(o.snapshot.discount_minor)}</small>}
                            </td>
                            <td>{clientName(o.profile_id)}</td>
                            <td>{money(o.total_minor)}</td>
                            <td>{money(o.paid_minor)}</td>
                            <td>
                              {money(o.balance_minor)}
                              {o.overdue_minor > 0 && <small className="danger-text">Просрочено {money(o.overdue_minor)}</small>}
                              {o.installments.map((p: Row, i: number) => (
                                <small key={i}>
                                  {date(p.due_at)} · {money(p.amount_minor)}
                                </small>
                              ))}
                            </td>
                            <td>
                              <div className="button-stack">
                                <button
                                  onClick={() =>
                                    void act(async () => {
                                      const r = await api('/orders/' + o.id + '/robokassa', 'POST');
                                      window.open(r.url, '_blank', 'noopener,noreferrer');
                                      setNotice(r.test ? 'Открыта тестовая оплата. Она не выдаёт рабочие права.' : 'Ссылка оплаты открыта');
                                    })
                                  }
                                >
                                  Ссылка оплаты
                                </button>
                                {me.role === 'owner' && (
                                  <>
                                    <button onClick={() => paymentForm(o)}>Учесть оплату</button>
                                    <button onClick={() => paymentForm(o, true)}>Возврат</button>
                                    <button onClick={() => grantForm(o)}>Право доступа</button>
                                    <button
                                      onClick={() =>
                                        setForm({
                                          title: 'Подтверждение обучения',
                                          fields: [{ key: 'evidence', label: 'Основание завершения', required: true }],
                                          save: async (v) => {
                                            await api('/orders/' + o.id + '/academic', 'POST', { complete: true, evidence: v.evidence });
                                            await load();
                                          },
                                        })
                                      }
                                    >
                                      Обучение завершено
                                    </button>
                                  </>
                                )}
                                <button
                                  onClick={() =>
                                    void act(async () => {
                                      await api('/orders/' + o.id + '/cancel', 'POST');
                                      await load();
                                    })
                                  }
                                >
                                  Отменить
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {!orders.length && <div className="empty">Создайте заказ по активному продукту из карточки клиента.</div>}
                  </section>
                  <section className="panel promo-panel">
                    <div className="panel-title">
                      <h2>Промокоды</h2>
                      {me.role === 'owner' && (
                        <button
                          onClick={() =>
                            setForm({
                              title: 'Создать промокод',
                              fields: [
                                { key: 'code', label: 'Код (пусто — сгенерировать)' },
                                { key: 'percent', label: 'Скидка, %', type: 'number', required: true },
                                { key: 'max_uses', label: 'Число заказов', type: 'number', value: '1' },
                              ],
                              save: async (v) => {
                                await api(`/projects/${pid}/promos`, 'POST', { code: v.code, percent: Number(v.percent), max_uses: Number(v.max_uses) });
                                await load();
                              },
                            })
                          }
                        >
                          + Промокод
                        </button>
                      )}
                    </div>
                    {promos.map((p) => (
                      <div className="direction" key={p.id}>
                        <b>{p.code}</b>
                        <span>−{p.percent}%</span>
                        <small>
                          Занято {p.reserved} из {p.max_uses}
                        </small>
                      </div>
                    ))}
                  </section>
                </>
              )}
              {section === 'campaigns' && (
                <section className="panel">
                  <h2>Приветственные кампании</h2>
                  <p className="muted">Выберите клиентов в таблице, затем создайте кампанию. Импорт контакта не подтверждает возможность отправки.</p>
                  {campaigns.map((c) => (
                    <article className="campaign" key={c.id}>
                      <div className="row">
                        <h3>{c.title}</h3>
                        <span className="badge">{stateNames[c.state]}</span>
                      </div>
                      <p>{c.text}</p>
                      <p className="fine">
                        {JSON.stringify(c.delivery)} {c.report.excluded?.length ? `Исключено: ${c.report.excluded.length}` : ''}
                      </p>
                      {c.report.recipients && (
                        <p className="fine">
                          Доступных диалогов: {c.report.recipients.filter((r: Row) => r.eligible).length} / {c.profile_ids.length}
                        </p>
                      )}
                      <div className="row">
                        {c.state === 'draft' && (
                          <button
                            className="primary"
                            onClick={() =>
                              void act(async () => {
                                await api('/campaigns/' + c.id + '/launch', 'POST');
                                await load();
                              })
                            }
                          >
                            Запустить отправку
                          </button>
                        )}
                        {['queued', 'paused'].includes(c.state) && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await api('/campaigns/' + c.id + '/control', 'POST', { action: c.state === 'paused' ? 'resume' : 'pause' });
                                await load();
                              })
                            }
                          >
                            {c.state === 'paused' ? 'Продолжить' : 'Пауза'}
                          </button>
                        )}
                        {c.state !== 'cancelled' && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await api('/campaigns/' + c.id + '/cancel', 'POST');
                                await load();
                              })
                            }
                          >
                            Остановить
                          </button>
                        )}
                      </div>
                    </article>
                  ))}
                  {!campaigns.length && <div className="empty">Кампаний пока нет</div>}
                </section>
              )}
              {section === 'outreach' && (
                <section className="panel">
                  <h2>Прогрев телефонной базы</h2>
                  <p className="muted">
                    Партия готовит текст и Entry-ссылку на официальный MAX/Telegram бот. Dry_run не использует Campaign/Outbox и не требует PyMax.
                    После Start контакт появляется в «Диалогах» через обычный Identity.
                  </p>
                  {!entries.length && (
                    <p className="fine">Сначала опубликуйте воронку и создайте Entry в редакторе воронок — без точки входа прогрев некуда вести.</p>
                  )}
                  {outreach.map((b) => (
                    <article className="campaign" key={b.id}>
                      <div className="row">
                        <h3>{b.title}</h3>
                        <span className="badge">{stateNames[b.state] || b.state}</span>
                        <span className="tag">{b.mode}</span>
                        <small>лимит {b.daily_cap}/сутки</small>
                      </div>
                      <p>{b.invite_text}</p>
                      <p className="fine">Статусы: {JSON.stringify(b.recipient_states || {})}</p>
                      <div className="row">
                        <button
                          onClick={() =>
                            void act(async () => {
                              setOutreachDetail(await api('/outreach-batches/' + b.id));
                            })
                          }
                        >
                          Получатели
                        </button>
                        {['draft', 'prepared', 'paused'].includes(b.state) && (
                          <button
                            className="primary"
                            onClick={() =>
                              void act(async () => {
                                const r = await api('/outreach-batches/' + b.id + '/prepare', 'POST');
                                setNotice(
                                  `Подготовлено: ${r.prepared}. Пропущено: ${r.skipped}. Outbox не использовался. Пример ссылки: ${r.bot_link_example || '—'}`,
                                );
                                await load();
                                setOutreachDetail(await api('/outreach-batches/' + b.id));
                              })
                            }
                          >
                            Dry_run: подготовить тексты
                          </button>
                        )}
                        {['draft', 'prepared'].includes(b.state) && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await api('/outreach-batches/' + b.id + '/control', 'POST', { action: 'pause' });
                                await load();
                              })
                            }
                          >
                            Пауза
                          </button>
                        )}
                        {b.state === 'paused' && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await api('/outreach-batches/' + b.id + '/control', 'POST', { action: 'resume' });
                                await load();
                              })
                            }
                          >
                            Продолжить
                          </button>
                        )}
                        {b.state !== 'cancelled' && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await api('/outreach-batches/' + b.id + '/control', 'POST', { action: 'cancel' });
                                await load();
                                setOutreachDetail(null);
                              })
                            }
                          >
                            Отменить
                          </button>
                        )}
                      </div>
                      {outreachDetail && outreachDetail.id === b.id && (
                        <div className="preview-scroll" style={{ marginTop: 12 }}>
                          {(outreachDetail.recipients || []).map((r: Row) => (
                            <div className="preview-row" key={r.id}>
                              <b>
                                {clientName(r.profile_id)} · {stateNames[r.state] || r.state}
                              </b>
                              <small>{r.phone}</small>
                              {r.bot_link && (
                                <small>
                                  <a href={r.bot_link} target="_blank" rel="noreferrer">
                                    {r.bot_link}
                                  </a>
                                </small>
                              )}
                              {r.invite_text && <p className="fine">{r.invite_text}</p>}
                              {r.last_error && <span className="danger-text">{r.last_error}</span>}
                              <div className="button-stack">
                                {['prepared', 'invited'].includes(r.state) && (
                                  <>
                                    <button
                                      onClick={() =>
                                        void act(async () => {
                                          await api(`/outreach-batches/${b.id}/recipients/${r.id}/outcome`, 'POST', {
                                            state: 'invited',
                                            note: 'Отправлено вручную / с отдельной SIM',
                                          });
                                          setOutreachDetail(await api('/outreach-batches/' + b.id));
                                          await load();
                                        })
                                      }
                                    >
                                      Отметить «приглашён»
                                    </button>
                                    <button
                                      onClick={() =>
                                        void act(async () => {
                                          await api(`/outreach-batches/${b.id}/recipients/${r.id}/outcome`, 'POST', {
                                            state: 'started',
                                            note: 'Пользователь нажал Start',
                                          });
                                          setOutreachDetail(await api('/outreach-batches/' + b.id));
                                          await load();
                                        })
                                      }
                                    >
                                      Start бота
                                    </button>
                                  </>
                                )}
                                {r.state !== 'stopped' && r.state !== 'started' && (
                                  <button
                                    onClick={() =>
                                      void act(async () => {
                                        await api(`/outreach-batches/${b.id}/recipients/${r.id}/outcome`, 'POST', {
                                          state: 'stopped',
                                          note: 'Отказ от outreach',
                                        });
                                        setOutreachDetail(await api('/outreach-batches/' + b.id));
                                        await load();
                                      })
                                    }
                                  >
                                    Отказ
                                  </button>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </article>
                  ))}
                  {!outreach.length && (
                    <div className="empty">
                      <h2>Партий прогрева пока нет</h2>
                      <p>Выберите клиентов с телефонами в таблице и нажмите «Прогрев (dry_run)».</p>
                    </div>
                  )}
                </section>
              )}
              {section === 'imports' && (
                <div className="import-layout">
                  <section className="panel">
                    <h2>1. Подготовьте данные</h2>
                    <p className="muted">CSV до 5 МБ и 5000 строк. UTF-8 или Windows-1251. Разделитель определяется автоматически.</p>
                    <label>
                      Файл CSV
                      <input
                        type="file"
                        accept=".csv,.txt"
                        onChange={(e) => {
                          setFile(e.target.files?.[0] || null);
                          setPreview(null);
                          setMapping({});
                        }}
                      />
                    </label>
                    <label>
                      Или вставьте таблицу с заголовками
                      <textarea
                        rows={7}
                        placeholder={'Имя;Email;Телефон\nАнна;anna@example.ru;+79991234567'}
                        value={paste}
                        onChange={(e) => {
                          setPaste(e.target.value);
                          setFile(null);
                          setPreview(null);
                          setMapping({});
                        }}
                      />
                    </label>
                    <button className="primary" disabled={busy || (!file && !paste)} onClick={() => void act(() => previewFile({}))}>
                      Проверить и сопоставить
                    </button>
                    <p className="fine">Имена и username не используются для автоматического слияния. Возможные дубли пропускаются без перезаписи.</p>
                  </section>
                  {preview && (
                    <section className="panel">
                      <h2>2. Проверьте соответствия</h2>
                      <div className="mapping">
                        {[
                          ['name', 'Имя'],
                          ['email', 'Email'],
                          ['phone', 'Телефон'],
                          ['company', 'Компания'],
                          ['source', 'Источник'],
                          ['external_id', 'Внешний ID'],
                          ['username', 'Username'],
                          ['notes', 'Заметка'],
                        ].map(([field, label]) => (
                          <label key={field}>
                            {label}
                            <select value={mapping[field] || ''} onChange={(e) => setMapping({ ...mapping, [field]: e.target.value })}>
                              <option value="">Не импортировать</option>
                              {preview.headers.map((h: string) => (
                                <option key={h}>{h}</option>
                              ))}
                            </select>
                          </label>
                        ))}
                      </div>
                      <button onClick={() => void act(() => previewFile(Object.fromEntries(Object.entries(mapping).filter(([, v]) => v))))}>
                        Обновить предпросмотр
                      </button>
                      <p>
                        {preview.total} строк · ошибок: {preview.errors} · возможных дублей: {preview.possible_duplicates}
                      </p>
                      <div className="preview-scroll">
                        {preview.rows.map((r: Row) => (
                          <div className="preview-row" key={r.line}>
                            <b>
                              #{r.line} {r.data.name || r.data.email || r.data.username}
                            </b>
                            <small>
                              {r.data.email} {r.data.phone}
                            </small>
                            {r.status === 'possible_duplicate' && <small>Возможный дубль — будет пропущен</small>}
                            {r.error && <span className="danger-text">{r.error}</span>}
                          </div>
                        ))}
                      </div>
                      <button
                        className="primary"
                        disabled={busy || preview.state === 'done'}
                        onClick={() =>
                          void act(async () => {
                            const r = await api('/imports/' + preview.id + '/commit', 'POST');
                            setPreview({ ...preview, state: 'done', report: r });
                            setNotice(`Создано: ${r.created}. Пропущено: ${r.skipped}. Ошибок: ${r.errors.length}. Сообщения не отправлялись.`);
                            await load();
                          })
                        }
                      >
                        {preview.state === 'done' ? 'Партия импортирована' : '3. Импортировать в базу'}
                      </button>
                      {preview.report?.errors?.map((e: Row) => (
                        <p key={e.line} className="fine">
                          Строка {e.line}: {e.error}
                        </p>
                      ))}
                    </section>
                  )}
                </div>
              )}
              {section === 'settings' && (
                <div className="two-column">
                  <section className="panel">
                    <div className="panel-title">
                      <h2>Команда</h2>
                      {me.role === 'owner' && (
                        <button
                          onClick={() =>
                            setForm({
                              title: 'Новый сотрудник',
                              fields: [
                                { key: 'login', label: 'Логин', required: true },
                                { key: 'password', label: 'Начальный пароль, от 12 символов', type: 'password', required: true },
                                { key: 'email', label: 'Email', type: 'email' },
                                {
                                  key: 'role',
                                  label: 'Роль',
                                  type: 'select',
                                  options: [
                                    ['manager', 'Аккаунт-менеджер'],
                                    ['editor', 'Партнёр'],
                                  ],
                                },
                              ],
                              save: async (v) => {
                                await api('/staff', 'POST', { ...v, project_ids: [pid] });
                                await load();
                              },
                            })
                          }
                        >
                          + Сотрудник
                        </button>
                      )}
                    </div>
                    {staff.map((s) => (
                      <div className="direction" key={s.id}>
                        <span className="avatar">{String(s.login).slice(0, 1)}</span>
                        <div>
                          <b>{s.login}</b>
                          <small>
                            {s.role} · {s.active ? 'активен' : 'отключён'}
                          </small>
                        </div>
                        {me.role === 'owner' && s.active && s.id !== me.id && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await api('/staff/' + s.id + '/disable', 'POST');
                                await load();
                              })
                            }
                          >
                            Отключить
                          </button>
                        )}
                      </div>
                    ))}
                  </section>
                  <section className="panel">
                    <div className="panel-title">
                      <h2>Подключения</h2>
                      {me.role === 'owner' && (
                        <button
                          onClick={() =>
                            void act(async () => {
                              const r = await api(`/projects/${pid}/email-connection`, 'POST');
                              setNotice(r.configured ? 'Email-подключение добавлено' : 'Email-подключение добавлено. Настройте SMTP на сервере.');
                              await load();
                            })
                          }
                        >
                          + Email
                        </button>
                      )}
                    </div>
                    {overview.connections?.map((c: Row) => (
                      <div className="direction" key={c.id}>
                        <b>{c.name}</b>
                        <span>{c.platform}</span>
                        <span className={c.active ? 'badge good' : 'badge'}>{c.active ? 'Включено' : 'Выключено'}</span>
                      </div>
                    ))}
                    <h3>Интеграции</h3>
                    {Object.entries(overview.integrations || {}).map(([key, value]) => (
                      <div className="direction" key={key}>
                        <b>{key.toUpperCase()}</b>
                        <small>
                          {value === true ? 'Параметры заданы; требуется проверка' : value === 'not_implemented' ? 'Адаптер LMS ещё не реализован' : 'Не настроено'}
                        </small>
                      </div>
                    ))}
                    <p className="fine">Секреты задаются на сервере. Наличие настроек не доказывает успешную доставку или доступность сервиса.</p>
                  </section>
                </div>
              )}
            </>
          )}
        </main>
      </div>
      {detail && (
        <div className="drawer-layer">
          <button className="scrim" aria-label="Закрыть фон карточки" onClick={() => setDetail(null)} />
          <aside className="drawer" role="dialog" aria-modal="true" aria-label="Карточка клиента">
            <div className="drawer-top">
              <span className="eyebrow">КАРТОЧКА КЛИЕНТА</span>
              <button aria-label="Закрыть карточку" onClick={() => setDetail(null)}>
                ✕
              </button>
            </div>
            <div className="contact-heading">
              <span className="avatar large">{detail.name.slice(0, 1)}</span>
              <h2>{detail.name}</h2>
              <p>{detail.company || 'Компания не указана'}</p>
            </div>
            <div className="row drawer-actions">
              <button onClick={() => contactForm(detail)}>Изменить</button>
              <button onClick={() => dealForm(detail)}>+ Сделка</button>
              <button onClick={() => taskForm(detail)}>+ Работа</button>
              <button disabled={!productOptions.length} onClick={() => orderForm(detail)}>
                + Заказ
              </button>
            </div>
            <dl>
              <dt>Email</dt>
              <dd>{detail.email || '—'}</dd>
              <dt>Телефон</dt>
              <dd>{detail.phone || '—'}</dd>
              <dt>Источник</dt>
              <dd>{detail.source}</dd>
              <dt>Коммуникации</dt>
              <dd>{detail.stopped ? 'Отказ от сообщений' : 'Проверять доступность выбранного канала'}</dd>
            </dl>
            <h3>Последний контакт</h3>
            <p>{detail.activities[0]?.outcome || 'Результат ещё не зафиксирован'}</p>
            <div className="row">
              <button
                onClick={() =>
                  setForm({
                    title: 'Зафиксировать контакт',
                    fields: [
                      { key: 'body', label: 'Содержание разговора', type: 'textarea', required: true },
                      { key: 'outcome', label: 'Результат / договорённость' },
                      {
                        key: 'channel',
                        label: 'Канал',
                        type: 'select',
                        options: [
                          ['manual', 'Заметка'],
                          ['phone', 'Телефон'],
                          ['meeting', 'Встреча'],
                          ['email', 'Email'],
                          ['telegram', 'Telegram'],
                          ['max', 'MAX'],
                          ['vk', 'ВК'],
                        ],
                      },
                    ],
                    save: async (v) => {
                      await api('/contacts/' + detail.id + '/activities', 'POST', v);
                      await refreshDetail();
                    },
                  })
                }
              >
                + Записать контакт
              </button>
              <button
                onClick={() =>
                  setForm({
                    title: 'Черновик с RAGA',
                    fields: [{ key: 'question', label: 'Что подготовить для клиента?', type: 'textarea', required: true }],
                    save: async (v) => {
                      await api('/contacts/' + detail.id + '/assistant', 'POST', v);
                      await refreshDetail();
                      setNotice('Черновик сохранён в истории. Клиенту ничего не отправлено.');
                    },
                  })
                }
              >
                Помощь ИИ
              </button>
            </div>
            <h3>Сделки и следующий шаг</h3>
            {detail.deals.map((d: Row) => (
              <div className="mini-card" key={d.id}>
                <b>{d.title}</b>
                <span className="tag">{me.stages[d.stage]}</span>
                <p>{d.next_action || 'Следующий шаг не назначен'}</p>
                <small>{date(d.due_at)}</small>
              </div>
            ))}
            <h3>Работа и обязательства</h3>
            {detail.tasks.map((t: Row) => (
              <div className="mini-card" key={t.id}>
                <b>{t.title}</b>
                <small>
                  {stateNames[t.state]} · {date(t.due_at)}
                </small>
                <p>{t.result}</p>
              </div>
            ))}
            <h3>Финансы</h3>
            {detail.orders.map((o: Row) => (
              <div className="mini-card" key={o.id}>
                <b>{o.snapshot.name}</b>
                <p>
                  Оплачено {money(o.paid_minor)} · осталось {money(o.balance_minor)}
                </p>
              </div>
            ))}
            {detail.identities.length > 0 && (
              <button
                onClick={() =>
                  setForm({
                    title: 'Ответ клиенту',
                    fields: [
                      {
                        key: 'identity_id',
                        label: 'Диалог',
                        type: 'select',
                        options: detail.identities.map((i: Row) => [i.id, i.name + ' · ' + i.connection_id]),
                      },
                      { key: 'text', label: 'Сообщение', type: 'textarea', required: true },
                    ],
                    save: async (v) => {
                      await api('/api/messages', 'POST', v);
                      setNotice('Ответ поставлен в очередь. Автоматический сценарий приостановлен.');
                      await refreshDetail();
                    },
                  })
                }
              >
                Ответить через подключение
              </button>
            )}
            <h3>История</h3>
            {detail.activities.map((a: Row) => (
              <article className="timeline" key={a.id}>
                <small>
                  {date(a.created_at)} · {a.channel} {a.kind === 'ai_draft' ? '· Черновик ИИ' : ''}
                </small>
                <b>{a.outcome}</b>
                <p>{a.body}</p>
              </article>
            ))}
            {detail.events.map((e: Row) => (
              <article className="timeline" key={e.id}>
                <small>
                  {date(e.created_at)} · {e.kind}
                </small>
                <p>{e.payload.text || e.payload.answer || ''}</p>
              </article>
            ))}
          </aside>
        </div>
      )}
      {form && (
        <FormModal
          form={form}
          busy={busy}
          error={error}
          onClose={() => setForm(null)}
          onSubmit={async (data) => {
            await act(async () => {
              await form.save(data);
              setForm(null);
            });
          }}
        />
      )}
    </div>
  );
}
