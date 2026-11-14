'use client';

import type { Row } from '../types';
import { money } from '../utils';

export function PaymentsSection({ data }: { data: Row | null }) {
  if (!data) {
    return (
      <section className="panel">
        <p className="fine">Загрузка статуса платёжных провайдеров…</p>
      </section>
    );
  }
  const rk = data.robokassa || {};
  const yk = data.yookassa || {};
  const stats = data.stats || {};
  return (
    <div className="two-column">
      <section className="panel">
        <div className="panel-title">
          <h2>Robokassa</h2>
          <span className={rk.ready ? 'badge good' : 'badge'}>{rk.ready ? 'Готов к режиму' : 'Не готов'}</span>
        </div>
        <dl className="status-dl">
          <dt>Login</dt>
          <dd>{rk.login_set ? 'задан' : 'не задан'}</dd>
          <dt>Режим</dt>
          <dd>{rk.mode === 'live' ? 'Боевой' : 'Тест'}</dd>
          <dt>Пароли режима</dt>
          <dd>{rk.passwords_set ? 'заданы' : 'не заданы'}</dd>
          <dt>ROBOKASSA_LIVE_READY</dt>
          <dd>{rk.live_ready ? '1' : '0'}</dd>
          <dt>Хеш</dt>
          <dd>{rk.hash}</dd>
          <dt>ResultURL</dt>
          <dd className="mono">{rk.result_url}</dd>
          <dt>Последний ResultURL</dt>
          <dd>
            {rk.last_result
              ? `${rk.last_result.state} · ${rk.last_result.created_at || '—'}`
              : 'ещё не было уведомлений'}
          </dd>
          <dt>Receipt в адаптере</dt>
          <dd>нет — фискализация вне Voronka</dd>
        </dl>
        <h3>Чек-лист фискализации</h3>
        <ul className="checklist">
          {(rk.fiscalization_checklist || []).map((item: Row) => (
            <li key={item.id}>
              <span className={item.done ? 'badge good' : 'badge'}>{item.done ? '✓' : '○'}</span> {item.label}
            </li>
          ))}
        </ul>
        <p className="fine">Секреты не показываются. Статистика ниже — только из Payment/Order.</p>
      </section>
      <div>
        <section className="panel">
          <div className="panel-title">
            <h2>YooKassa</h2>
            <span className="badge">каркас</span>
          </div>
          <p className="muted">{yk.note}</p>
          <dl className="status-dl">
            <dt>Adapter</dt>
            <dd>{yk.adapter}</dd>
            <dt>YOOKASSA_ENABLED</dt>
            <dd>{yk.enabled ? '1 (intent)' : '0'}</dd>
            <dt>Webhook</dt>
            <dd className="mono">{yk.webhook_url}</dd>
          </dl>
          <h3>Переменные окружения</h3>
          <ul className="checklist">
            {Object.entries(yk.env_names || {}).map(([name, hint]) => (
              <li key={name}>
                <span className={yk.env_present?.[name] ? 'badge good' : 'badge'}>
                  {yk.env_present?.[name] ? 'задан' : 'пусто'}
                </span>{' '}
                <code>{name}</code> — {String(hint)}
              </li>
            ))}
          </ul>
          <p className="fine">POST webhook отвечает 501, пока adapter не реализован.</p>
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
          <p className="fine">Источник: {stats.source || 'Payment/Order'}</p>
        </section>
      </div>
    </div>
  );
}
