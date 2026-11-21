'use client';

import { PORTAL_URLS } from '../portals';
import type { Row } from '../types';

/** Honest sandbox vs live gates — never a flip control for live money / mass marketing. */
export function SalesRunbookPanel({
  data,
  compact = false,
}: {
  data: Row | null;
  compact?: boolean;
}) {
  if (!data) {
    return (
      <section className="panel sales-runbook" aria-label="Sales runbook">
        <p className="fine">Загрузка статусов sandbox / live…</p>
      </section>
    );
  }

  const gates = (data.gates || {}) as Row;
  const rk = (gates.robokassa_live || {}) as Row;
  const mail = (gates.production_mail || {}) as Row;
  const mkt = (gates.production_marketing || {}) as Row;
  const sandbox = (gates.sandbox_payments || {}) as Row;
  const links = (data.links || {}) as Row;
  const next = Array.isArray(data.next_steps) ? data.next_steps : [];

  return (
    <section className={`panel sales-runbook${compact ? ' compact-runbook' : ''}`} aria-label="Sales runbook">
      <div className="panel-title">
        <h2>Sales runbook · mail live / pay sandbox</h2>
        <span className="badge">{data.policy || 'Q11=C: mail live; Robokassa sandbox'}</span>
      </div>
      <p className="muted">
        Честные ворота: mail может быть live (Q11=C), Robokassa — sandbox. Live-деньги и mass marketing из UI не
        включаются.
      </p>
      <ul className="checklist runbook-gates">
        <li>
          <span className={rk.on ? 'badge warn' : 'badge good'}>{rk.on ? 'LIVE ON' : 'OFF'}</span>
          <div>
            <b>Robokassa live</b>
            <small>{String(rk.summary || 'ROBOKASSA_LIVE_READY не включён')}</small>
          </div>
        </li>
        <li>
          <span className={mail.on ? 'badge good' : 'badge'}>{mail.on ? 'ON' : 'OFF'}</span>
          <div>
            <b>Production mail</b>
            <small>{String(mail.summary || 'SMTP/Mailu transactional path')}</small>
          </div>
        </li>
        <li>
          <span className={mkt.on ? 'badge warn' : 'badge good'}>{mkt.on ? 'ON' : 'OFF'}</span>
          <div>
            <b>Mass marketing</b>
            <small>{String(mkt.summary || 'Массовая production-отправка выключена')}</small>
          </div>
        </li>
        <li>
          <span className={sandbox.on ? 'badge good' : 'badge'}>{sandbox.on ? 'OK' : '—'}</span>
          <div>
            <b>Sandbox payments</b>
            <small>{String(sandbox.summary || 'Тестовый режим Robokassa')}</small>
          </div>
        </li>
      </ul>
      {!compact && next.length > 0 && (
        <>
          <h3>Что делать оператору</h3>
          <ol className="runbook-steps">
            {next.map((step: string, i: number) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </>
      )}
      <div className="empty-world runbook-links" aria-label="Deep-links">
        <a href={String(links.payments || '/crm/#payments')} rel="noreferrer">
          Платежи CRM ↗
        </a>
        <a href={String(links.signal || PORTAL_URLS.signal + '/')} target="_blank" rel="noreferrer">
          SIGNAL студия ↗
        </a>
        <a href={String(links.lms || PORTAL_URLS.lms + '/')} target="_blank" rel="noreferrer">
          LMS ↗
        </a>
        <a href={String(links.ops || PORTAL_URLS.voronka + '/ops/')} target="_blank" rel="noreferrer">
          Voronka ops ↗
        </a>
      </div>
      <p className="fine">
        UI не выставляет <code>ROBOKASSA_LIVE_READY</code> и не включает mass production marketing. Mail live ≠ live
        money.
      </p>
    </section>
  );
}
