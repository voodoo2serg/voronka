'use client';

import { useEffect, useState } from 'react';
import type { ApiFn, Row } from '../types';
import { money } from '../utils';

export function PaymentsSection({
  data,
  api,
  busy,
  onBusy,
  onNotice,
  isOwner,
}: {
  data: Row | null;
  api: ApiFn;
  busy: boolean;
  onBusy: (fn: () => Promise<void>) => Promise<void>;
  onNotice: (v: string) => void;
  isOwner: boolean;
}) {
  const [creds, setCreds] = useState<Row | null>(null);
  const [alerts, setAlerts] = useState<Row[]>([]);
  const [rk, setRk] = useState({
    login: '',
    test_mode: true,
    test_password1: '',
    test_password2: '',
    hash: 'sha256',
    receipt_tax: 'none',
  });
  const [yk, setYk] = useState({
    enabled: false,
    shop_id: '',
    secret_key: '',
    webhook_secret: '',
  });
  const [alertKind, setAlertKind] = useState<'paid' | 'not_paid' | 'amount_threshold'>('paid');
  const [alertAmount, setAlertAmount] = useState('0');
  const [alertChannel, setAlertChannel] = useState<'telegram' | 'email' | 'both'>('telegram');

  async function loadOwnerExtras() {
    if (!isOwner) return;
    const [c, a] = await Promise.all([
      api('/payment-providers/credentials'),
      api('/staff-alert-rules'),
    ]);
    setCreds(c);
    setAlerts(a.items || []);
    const rf = c.robokassa?.fields || {};
    setRk((prev) => ({
      ...prev,
      test_mode: rf.test_mode !== false,
      hash: String(rf.hash || 'sha256'),
      receipt_tax: String(rf.receipt_tax || 'none'),
    }));
    const yf = c.yookassa?.fields || {};
    setYk((prev) => ({
      ...prev,
      enabled: Boolean(yf.enabled),
    }));
  }

  useEffect(() => {
    void loadOwnerExtras().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOwner]);

  if (!data) {
    return (
      <section className="panel">
        <p className="fine">Загрузка статуса платёжных провайдеров…</p>
      </section>
    );
  }
  const rkStatus = data.robokassa || {};
  const ykStatus = data.yookassa || {};
  const stats = data.stats || {};

  return (
    <div className="two-column">
      <section className="panel">
        <div className="panel-title">
          <h2>Robokassa</h2>
          <span className={rkStatus.ready ? 'badge good' : 'badge'}>{rkStatus.ready ? 'Готов к режиму' : 'Не готов'}</span>
        </div>
        <dl className="status-dl">
          <dt>Login</dt>
          <dd>{rkStatus.login_set ? 'задан' : 'не задан'}</dd>
          <dt>Режим</dt>
          <dd>{rkStatus.mode === 'live' ? 'Боевой' : 'Тест / песочница'}</dd>
          <dt>Пароли режима</dt>
          <dd>{rkStatus.passwords_set ? 'заданы' : 'не заданы'}</dd>
          <dt>ROBOKASSA_LIVE_READY</dt>
          <dd>{rkStatus.live_ready ? '1' : '0'}</dd>
          <dt>ResultURL</dt>
          <dd className="mono">{rkStatus.result_url}</dd>
        </dl>
        {isOwner && (
          <form
            className="creds-form"
            onSubmit={(e) => {
              e.preventDefault();
              void onBusy(async () => {
                const fields: Record<string, string> = {
                  test_mode: rk.test_mode ? '1' : '0',
                  hash: rk.hash,
                  receipt_tax: rk.receipt_tax,
                };
                if (rk.login.trim()) fields.login = rk.login.trim();
                if (rk.test_password1.trim()) fields.test_password1 = rk.test_password1.trim();
                if (rk.test_password2.trim()) fields.test_password2 = rk.test_password2.trim();
                await api('/payment-providers/credentials', 'PUT', { provider: 'robokassa', fields });
                onNotice('Robokassa: учётные данные сохранены в vault (секреты не показываются)');
                setRk((p) => ({ ...p, login: '', test_password1: '', test_password2: '' }));
                await loadOwnerExtras();
              });
            }}
          >
            <h3>Учётные данные</h3>
            <label>
              Login магазина
              <input value={rk.login} onChange={(e) => setRk({ ...rk, login: e.target.value })} placeholder={rkStatus.login_set ? 'задан — оставьте пустым' : ''} />
            </label>
            <label>
              Тестовый пароль #1
              <input type="password" value={rk.test_password1} onChange={(e) => setRk({ ...rk, test_password1: e.target.value })} />
            </label>
            <label>
              Тестовый пароль #2
              <input type="password" value={rk.test_password2} onChange={(e) => setRk({ ...rk, test_password2: e.target.value })} />
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={rk.test_mode} onChange={(e) => setRk({ ...rk, test_mode: e.target.checked })} />
              Песочница (ROBOKASSA_TEST=1)
            </label>
            <label>
              Hash
              <select value={rk.hash} onChange={(e) => setRk({ ...rk, hash: e.target.value })}>
                <option value="md5">md5</option>
                <option value="sha256">sha256</option>
                <option value="sha512">sha512</option>
              </select>
            </label>
            <button className="primary" disabled={busy}>
              Сохранить Robokassa
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void onBusy(async () => {
                  const r = await api('/payment-providers/robokassa/test-ping', 'POST');
                  onNotice(r.ok ? 'Ping OK — credentials режима на месте' : 'Ping: не хватает login/паролей');
                })
              }
            >
              Test ping
            </button>
          </form>
        )}
        <p className="fine">
          Секреты не показываются. <code>ROBOKASSA_LIVE_READY</code> не включается из UI. Песочница OK.
        </p>
      </section>
      <div>
        <section className="panel">
          <div className="panel-title">
            <h2>ЮKassa</h2>
            <span className={ykStatus.ready ? 'badge good' : 'badge'}>{ykStatus.adapter || 'webhook_v1'}</span>
          </div>
          <p className="muted">{ykStatus.note}</p>
          <dl className="status-dl">
            <dt>Webhook</dt>
            <dd className="mono">{ykStatus.webhook_url}</dd>
            <dt>YOOKASSA_ENABLED</dt>
            <dd>{ykStatus.enabled ? '1' : '0'}</dd>
          </dl>
          {isOwner && (
            <form
              className="creds-form"
              onSubmit={(e) => {
                e.preventDefault();
                void onBusy(async () => {
                  const fields: Record<string, string> = {
                    enabled: yk.enabled ? '1' : '0',
                  };
                  if (yk.shop_id.trim()) fields.shop_id = yk.shop_id.trim();
                  if (yk.secret_key.trim()) fields.secret_key = yk.secret_key.trim();
                  if (yk.webhook_secret.trim()) fields.webhook_secret = yk.webhook_secret.trim();
                  await api('/payment-providers/credentials', 'PUT', { provider: 'yookassa', fields });
                  onNotice('ЮKassa: учётные данные сохранены');
                  setYk((p) => ({ ...p, shop_id: '', secret_key: '', webhook_secret: '' }));
                  await loadOwnerExtras();
                });
              }}
            >
              <h3>Учётные данные</h3>
              <label>
                Shop ID
                <input value={yk.shop_id} onChange={(e) => setYk({ ...yk, shop_id: e.target.value })} />
              </label>
              <label>
                Secret key
                <input type="password" value={yk.secret_key} onChange={(e) => setYk({ ...yk, secret_key: e.target.value })} />
              </label>
              <label>
                Webhook secret
                <input type="password" value={yk.webhook_secret} onChange={(e) => setYk({ ...yk, webhook_secret: e.target.value })} />
              </label>
              <label className="checkbox">
                <input type="checkbox" checked={yk.enabled} onChange={(e) => setYk({ ...yk, enabled: e.target.checked })} />
                Intent включения (не live-gate)
              </label>
              <button className="primary" disabled={busy}>
                Сохранить ЮKassa
              </button>
            </form>
          )}
        </section>
        <section className="panel" style={{ marginTop: 22 }}>
          <div className="panel-title">
            <h2>Статистика платежей</h2>
          </div>
          <div className="metrics compact-metrics">
            <article>
              <p>Заказы</p>
              <strong>{String(stats.orders_count || 0)}</strong>
            </article>
            <article>
              <p>Поступления</p>
              <strong>{money(stats.received_minor)}</strong>
            </article>
            <article>
              <p>Возвраты</p>
              <strong>{money(stats.refunded_minor)}</strong>
            </article>
            <article>
              <p>Платежей</p>
              <strong>{String(stats.payments_count || 0)}</strong>
            </article>
          </div>
        </section>
        {isOwner && (
          <section className="panel" style={{ marginTop: 22 }}>
            <div className="panel-title">
              <h2>Оповещения владельцу</h2>
            </div>
            <form
              className="creds-form"
              onSubmit={(e) => {
                e.preventDefault();
                void onBusy(async () => {
                  await api('/staff-alert-rules', 'POST', {
                    kind: alertKind,
                    channel: alertChannel,
                    amount_minor: Number(alertAmount) || 0,
                    active: true,
                  });
                  onNotice('Правило оповещения создано');
                  await loadOwnerExtras();
                });
              }}
            >
              <label>
                Событие
                <select value={alertKind} onChange={(e) => setAlertKind(e.target.value as typeof alertKind)}>
                  <option value="paid">Оплачено</option>
                  <option value="not_paid">Не оплачено</option>
                  <option value="amount_threshold">Сумма ≥ N</option>
                </select>
              </label>
              <label>
                Канал
                <select value={alertChannel} onChange={(e) => setAlertChannel(e.target.value as typeof alertChannel)}>
                  <option value="telegram">Telegram</option>
                  <option value="email">Email</option>
                  <option value="both">Оба</option>
                </select>
              </label>
              {alertKind === 'amount_threshold' && (
                <label>
                  Порог (копейки)
                  <input value={alertAmount} onChange={(e) => setAlertAmount(e.target.value)} />
                </label>
              )}
              <button className="primary" disabled={busy}>
                Добавить правило
              </button>
            </form>
            <ul className="checklist">
              {alerts.map((a) => (
                <li key={String(a.id)}>
                  <span className={a.active ? 'badge good' : 'badge'}>{String(a.kind)}</span>{' '}
                  {String(a.channel)}
                  {a.kind === 'amount_threshold' ? ` · ≥ ${money(a.amount_minor)}` : ''}
                </li>
              ))}
            </ul>
            {creds?.live_money_note && <p className="fine">{String(creds.live_money_note)}</p>}
          </section>
        )}
      </div>
    </div>
  );
}
