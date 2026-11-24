'use client';

import { useEffect, useState } from 'react';
import { voronkaOpsBotsHref } from '../portals';
import type { ApiFn, Row } from '../types';

const TYPE_OPTIONS: { id: string; label: string }[] = [
  { id: 'telegram_bot', label: 'Telegram бот' },
  { id: 'telegram_channel', label: 'Telegram канал' },
  { id: 'max_bot', label: 'MAX бот' },
  { id: 'email_smtp', label: 'Email (SMTP)' },
  { id: 'robokassa', label: 'Robokassa' },
  { id: 'yookassa', label: 'ЮKassa' },
];

/** Owner create form: no email_smtp — use «Новая почта» (Mailu one-click). */
const CREATE_TYPE_OPTIONS = TYPE_OPTIONS.filter((t) => t.id !== 'email_smtp');

const DEFAULT_MAIL_DOMAINS = ['solo-pr.ru', 'vodopetov.ru'];

type FormState = {
  type: string;
  label: string;
  meta: Record<string, string>;
  secrets: Record<string, string>;
};

const emptyForm = (): FormState => ({
  type: 'telegram_bot',
  label: '',
  meta: {},
  secrets: {},
});

function secretFieldsFor(type: string): { key: string; label: string }[] {
  switch (type) {
    case 'telegram_bot':
      return [
        { key: 'bot_token', label: 'Токен бота' },
        { key: 'webhook_secret', label: 'Webhook secret (пусто = авто)' },
      ];
    case 'max_bot':
      return [
        { key: 'token', label: 'Токен' },
        { key: 'webhook_secret', label: 'Webhook secret (пусто = авто)' },
      ];
    case 'email_smtp':
      // Rotate only — create goes through «Новая почта» (no owner SMTP form).
      return [{ key: 'smtp_password', label: 'SMTP пароль / app-token' }];
    case 'robokassa':
      return [
        { key: 'test_password1', label: 'Тест пароль #1' },
        { key: 'test_password2', label: 'Тест пароль #2' },
        { key: 'password1', label: 'Боевой пароль #1' },
        { key: 'password2', label: 'Боевой пароль #2' },
      ];
    case 'yookassa':
      return [
        { key: 'secret_key', label: 'Secret key' },
        { key: 'webhook_secret', label: 'Webhook secret' },
      ];
    default:
      return [];
  }
}

function metaFieldsFor(type: string): { key: string; label: string }[] {
  switch (type) {
    case 'telegram_bot':
    case 'max_bot':
      return [{ key: 'bot_username', label: 'Username бота (без @)' }];
    case 'telegram_channel':
      return [
        { key: 'invite_url', label: 'Invite URL' },
        { key: 'chat_id', label: 'chat_id (опц.)' },
        { key: 'linked_bot_vault_id', label: 'ID бота из vault (опц.)' },
      ];
    case 'robokassa':
      return [
        { key: 'login', label: 'Login магазина' },
        { key: 'shop_id', label: 'Shop id (опц.)' },
        { key: 'hash', label: 'Hash (md5/sha256/sha512)' },
      ];
    case 'yookassa':
      return [{ key: 'shop_id', label: 'Shop id' }];
    default:
      return [];
  }
}

export function ConnectionsVaultSection({
  api,
  pid,
  busy,
  onBusy,
  onNotice,
  isOwner,
}: {
  api: ApiFn;
  pid: string;
  busy: boolean;
  onBusy: (fn: () => Promise<void>) => Promise<void>;
  onNotice: (v: string) => void;
  isOwner: boolean;
}) {
  const [items, setItems] = useState<Row[]>([]);
  const [form, setForm] = useState<FormState>(emptyForm());
  const [showCreate, setShowCreate] = useState(false);
  const [showMailbox, setShowMailbox] = useState(false);
  const [mailDomains, setMailDomains] = useState<string[]>(DEFAULT_MAIL_DOMAINS);
  const [mailboxDomain, setMailboxDomain] = useState(DEFAULT_MAIL_DOMAINS[0]);
  const [mailboxLocal, setMailboxLocal] = useState('');
  const [rotateId, setRotateId] = useState('');
  const [rotateSecrets, setRotateSecrets] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState('');

  async function refresh() {
    if (!pid) return;
    const data = await api(`/projects/${pid}/connection-vault?include_inactive=1`);
    setItems(data.items || []);
    const domains = Array.isArray(data.mail_domains) ? data.mail_domains.map(String) : [];
    if (domains.length) {
      setMailDomains(domains);
      if (!domains.includes(mailboxDomain)) setMailboxDomain(domains[0]);
    }
  }

  useEffect(() => {
    void onBusy(refresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  if (!pid) {
    return (
      <section className="panel">
        <p className="muted">Выберите проект, чтобы открыть Подключения.</p>
      </section>
    );
  }

  const filtered = filter ? items.filter((i) => String(i.type) === filter) : items;

  return (
    <div className="two-column">
      <section className="panel">
        <div className="panel-title">
          <h2>Подключения</h2>
          <div className="constructor-actions">
            {isOwner && (
              <>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void onBusy(async () => {
                      const r = await api(`/projects/${pid}/connection-vault/sync-legacy`, 'POST');
                      onNotice(`Синхронизация legacy: +${r.created || 0}`);
                      await refresh();
                    })
                  }
                >
                  Импорт legacy
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={busy}
                  onClick={() => {
                    setShowMailbox(true);
                    setShowCreate(false);
                  }}
                >
                  Новая почта
                </button>
                <button type="button" disabled={busy} onClick={() => setShowCreate(true)}>
                  Создать
                </button>
              </>
            )}
          </div>
        </div>
        <p className="fine">
          Боты, почта, каналы и кассы проекта. Почту создавайте кнопкой «Новая почта» на доменах{' '}
          {mailDomains.join(' / ')} — SMTP host/port/пароль не нужны. Секреты только как ••••last4.
        </p>
        <label>
          Тип
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">Все</option>
            {TYPE_OPTIONS.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <ul className="constructor-list vault-list">
          {filtered.map((row) => {
            const masks = (row.secret_masks as Record<string, string>) || {};
            const meta = (row.meta as Record<string, unknown>) || {};
            return (
              <li key={String(row.id)} className={!row.active ? 'muted' : undefined}>
                <div>
                  <b>{String(row.label)}</b>
                  <small>
                    {String(row.type_label || row.type)}
                    {row.has_secret ? ' · секрет задан' : ' · без секрета'}
                    {!row.active ? ' · выкл.' : ''}
                    {row.legacy_source ? ` · ${String(row.legacy_source)}` : ''}
                  </small>
                  <div className="fine mono">
                    {Object.entries(masks).map(([k, v]) => (
                      <span key={k} style={{ marginRight: 12 }}>
                        {k}: {v}
                      </span>
                    ))}
                    {Object.entries(meta)
                      .filter(([, v]) => v !== '' && v != null)
                      .map(([k, v]) => (
                        <span key={k} style={{ marginRight: 12 }}>
                          {k}: {String(v)}
                        </span>
                      ))}
                  </div>
                </div>
                {(String(row.type) === 'telegram_bot' || isOwner) && (
                  <div className="constructor-actions">
                    {String(row.type) === 'telegram_bot' && (
                      <a
                        className="primary"
                        href={voronkaOpsBotsHref(pid)}
                        target="_blank"
                        rel="noreferrer"
                        title="Открыть ops → Боты (Activate webhook)"
                      >
                        Активировать webhook
                      </a>
                    )}
                    {isOwner && (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => {
                            setRotateId(String(row.id));
                            setRotateSecrets({});
                          }}
                        >
                          Сменить секрет
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void onBusy(async () => {
                              await api(`/connection-vault/${row.id}`, 'PATCH', { active: !row.active });
                              onNotice(row.active ? 'Отключено' : 'Включено');
                              await refresh();
                            })
                          }
                        >
                          {row.active ? 'Выкл.' : 'Вкл.'}
                        </button>
                      </>
                    )}
                  </div>
                )}
              </li>
            );
          })}
          {!filtered.length && <li className="muted">Пока нет подключений для этого проекта.</li>}
        </ul>
      </section>

      {isOwner && showMailbox && (
        <section className="panel">
          <div className="panel-title">
            <h2>Новая почта</h2>
            <button type="button" className="link" onClick={() => setShowMailbox(false)}>
              Закрыть
            </button>
          </div>
          <p className="fine">
            Создаёт ящик в Mailu и подключение From для конструктора. Host, порт и пароль подставляет
            платформа — вводить их не нужно.
          </p>
          <form
            className="creds-form"
            onSubmit={(e) => {
              e.preventDefault();
              void onBusy(async () => {
                const local = mailboxLocal.trim().toLowerCase().replace(/@.*$/, '');
                if (!local) throw new Error('Укажите имя ящика');
                const created = await api(`/projects/${pid}/connection-vault/mailboxes`, 'POST', {
                  domain: mailboxDomain,
                  local_part: local,
                  label: `${local}@`,
                });
                const addr = String(created.from_address || `${local}@${mailboxDomain}`);
                onNotice(`Почта создана: ${addr} — доступна как From`);
                setMailboxLocal('');
                setShowMailbox(false);
                await refresh();
              });
            }}
          >
            <label>
              Домен
              <select value={mailboxDomain} onChange={(e) => setMailboxDomain(e.target.value)}>
                {mailDomains.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Имя
              <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input
                  required
                  value={mailboxLocal}
                  onChange={(e) => setMailboxLocal(e.target.value)}
                  placeholder="serg"
                  autoComplete="off"
                  style={{ flex: 1 }}
                />
                <span className="mono">@{mailboxDomain}</span>
              </span>
            </label>
            <button className="primary" disabled={busy || !mailboxLocal.trim()}>
              Создать почту
            </button>
          </form>
        </section>
      )}

      {isOwner && showCreate && (
        <section className="panel">
          <div className="panel-title">
            <h2>Новое подключение</h2>
            <button type="button" className="link" onClick={() => setShowCreate(false)}>
              Закрыть
            </button>
          </div>
          <p className="fine">
            Для почты используйте «Новая почта». Здесь — боты, каналы и кассы (без SMTP-форм).
          </p>
          <form
            className="creds-form"
            onSubmit={(e) => {
              e.preventDefault();
              void onBusy(async () => {
                const secrets: Record<string, string> = {};
                for (const [k, v] of Object.entries(form.secrets)) {
                  if (v.trim()) secrets[k] = v.trim();
                }
                const meta: Record<string, string> = {};
                for (const [k, v] of Object.entries(form.meta)) {
                  if (v.trim()) meta[k] = v.trim();
                }
                const created = await api(`/projects/${pid}/connection-vault`, 'POST', {
                  type: form.type,
                  label: form.label.trim(),
                  meta,
                  secrets,
                });
                const dumped = JSON.stringify(created);
                for (const v of Object.values(secrets)) {
                  if (v && dumped.includes(v)) {
                    throw new Error('Секрет попал в ответ API — отказ');
                  }
                }
                onNotice('Подключение создано (секреты замаскированы)');
                setForm(emptyForm());
                setShowCreate(false);
                await refresh();
              });
            }}
          >
            <label>
              Тип
              <select
                value={form.type}
                onChange={(e) => setForm({ ...emptyForm(), type: e.target.value, label: form.label })}
              >
                {CREATE_TYPE_OPTIONS.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Название
              <input
                required
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                placeholder="Например Solo_pr_bot"
              />
            </label>
            {metaFieldsFor(form.type).map((f) => (
              <label key={f.key}>
                {f.label}
                <input
                  value={form.meta[f.key] || ''}
                  onChange={(e) => setForm({ ...form, meta: { ...form.meta, [f.key]: e.target.value } })}
                />
              </label>
            ))}
            {secretFieldsFor(form.type).map((f) => (
              <label key={f.key}>
                {f.label}
                <input
                  type="password"
                  autoComplete="new-password"
                  value={form.secrets[f.key] || ''}
                  onChange={(e) =>
                    setForm({ ...form, secrets: { ...form.secrets, [f.key]: e.target.value } })
                  }
                />
              </label>
            ))}
            <button className="primary" disabled={busy || !form.label.trim()}>
              Сохранить
            </button>
          </form>
        </section>
      )}

      {isOwner && rotateId && (
        <section className="panel">
          <div className="panel-title">
            <h2>Сменить секрет</h2>
            <button type="button" className="link" onClick={() => setRotateId('')}>
              Закрыть
            </button>
          </div>
          <form
            className="creds-form"
            onSubmit={(e) => {
              e.preventDefault();
              void onBusy(async () => {
                const secrets: Record<string, string> = {};
                for (const [k, v] of Object.entries(rotateSecrets)) {
                  if (v.trim()) secrets[k] = v.trim();
                }
                const row = items.find((i) => String(i.id) === rotateId);
                const rotated = await api(`/connection-vault/${rotateId}/rotate`, 'POST', { secrets });
                const dumped = JSON.stringify(rotated);
                for (const v of Object.values(secrets)) {
                  if (v && dumped.includes(v)) throw new Error('Секрет попал в ответ API — отказ');
                }
                onNotice(`Секрет обновлён · ${row?.label || rotateId.slice(0, 8)}`);
                setRotateId('');
                setRotateSecrets({});
                await refresh();
              });
            }}
          >
            <p className="fine">Пустые поля не меняют текущий секрет. После сохранения снова только маска.</p>
            {secretFieldsFor(String(items.find((i) => String(i.id) === rotateId)?.type || '')).map((f) => (
              <label key={f.key}>
                {f.label}
                <input
                  type="password"
                  autoComplete="new-password"
                  value={rotateSecrets[f.key] || ''}
                  onChange={(e) => setRotateSecrets({ ...rotateSecrets, [f.key]: e.target.value })}
                  placeholder="••••"
                />
              </label>
            ))}
            <button className="primary" disabled={busy}>
              Сохранить новый секрет
            </button>
          </form>
        </section>
      )}

      {!isOwner && (
        <section className="panel">
          <p className="fine">Просмотр списка доступен. Создавать и менять секреты может только владелец.</p>
        </section>
      )}
    </div>
  );
}
