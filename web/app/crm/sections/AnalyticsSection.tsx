'use client';

import type { Row } from '../types';
import { money } from '../utils';
import { PORTAL_URLS } from '../portals';

export function AnalyticsSection({
  report,
  from,
  to,
  onFrom,
  onTo,
  onLoad,
  busy,
}: {
  report: Row | null;
  from: string;
  to: string;
  onFrom: (v: string) => void;
  onTo: (v: string) => void;
  onLoad: () => void;
  busy: boolean;
}) {
  return (
    <div>
      <section className="panel" style={{ marginBottom: 22 }}>
        <div className="panel-title">
          <h2>Период</h2>
          <button className="primary" disabled={busy} onClick={onLoad}>
            Построить отчёт
          </button>
        </div>
        <div className="row" style={{ gap: 16 }}>
          <label style={{ marginBottom: 0 }}>
            С
            <input type="date" value={from} onChange={(e) => onFrom(e.target.value)} />
          </label>
          <label style={{ marginBottom: 0 }}>
            По
            <input type="date" value={to} onChange={(e) => onTo(e.target.value)} />
          </label>
        </div>
        <p className="fine">CSV export — follow-up P2. LMS-стата не фейкается.</p>
      </section>
      {!report ? (
        <section className="empty compact">
          <h2>Выберите период</h2>
          <p>Отчёт по деньгам, контактам, обучениям и коммуникациям за выбранные даты.</p>
        </section>
      ) : (
        <>
          <div className="metrics">
            <article>
              <p>Деньги · нетто</p>
              <strong>{money(report.money?.net_minor)}</strong>
            </article>
            <article>
              <p>Новые контакты</p>
              <strong>{String(report.contacts?.new || 0)}</strong>
            </article>
            <article>
              <p>Покупатели (всего)</p>
              <strong>{String(report.contacts?.purchasers_total || 0)}</strong>
            </article>
            <article>
              <p>Outbox sent</p>
              <strong>{String(report.comms?.outbox_sent || 0)}</strong>
            </article>
          </div>
          <div className="two-column">
            <section className="panel">
              <h2>Деньги</h2>
              <dl className="status-dl">
                <dt>Заказы в периоде</dt>
                <dd>{report.money?.orders_in_period ?? 0}</dd>
                <dt>Поступления</dt>
                <dd>{money(report.money?.received_minor)}</dd>
                <dt>Возвраты</dt>
                <dd>{money(report.money?.refunded_minor)}</dd>
                <dt>Платежей</dt>
                <dd>{report.money?.payments_count ?? 0}</dd>
              </dl>
            </section>
            <section className="panel">
              <h2>Контакты / источники</h2>
              <dl className="status-dl">
                <dt>Новые</dt>
                <dd>{report.contacts?.new ?? 0}</dd>
                {Object.entries(report.contacts?.by_source || {}).map(([src, n]) => (
                  <span key={src} style={{ display: 'contents' }}>
                    <dt>{src}</dt>
                    <dd>{String(n)}</dd>
                  </span>
                ))}
              </dl>
            </section>
            <section className="panel">
              <h2>Обучение (RO)</h2>
              {report.learning?.available ? (
                <p>Есть события LMS</p>
              ) : (
                <div className="empty compact">
                  <p>{report.learning?.note}</p>
                  <a href={report.learning?.lms_portal || PORTAL_URLS.lms} target="_blank" rel="noreferrer">
                    Открыть LMS ↗
                  </a>
                </div>
              )}
            </section>
            <section className="panel">
              <h2>Коммуникации</h2>
              <dl className="status-dl">
                <dt>Кампании</dt>
                <dd>{report.comms?.campaigns_total ?? 0}</dd>
                <dt>sent / failed / unknown</dt>
                <dd>
                  {report.comms?.outbox_sent ?? 0} / {report.comms?.outbox_failed ?? 0} /{' '}
                  {report.comms?.outbox_unknown ?? 0}
                </dd>
                <dt>Прогрев (партии)</dt>
                <dd>{report.comms?.outreach_batches_in_period ?? 0}</dd>
              </dl>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
