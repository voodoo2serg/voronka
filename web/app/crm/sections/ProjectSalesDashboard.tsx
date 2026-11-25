'use client';

import type { Row } from '../types';
import { date, money } from '../utils';

type Props = {
  dashboard: Row | null;
  overview: Row;
  deals: Row[];
  startProjectId?: string;
  busy: boolean;
  onOpenClients: () => void;
  onOpenDeals: () => void;
  onOpenChains: () => void;
  onOpenContact: (id: string) => void;
  onConvertLead: (lead: Row) => void;
  clientName: (id: string) => string;
};

/** CRM-R7: project interior = sales / deals / clients lens (not WorkToday lead wall). */
export function ProjectSalesDashboard({
  dashboard,
  overview,
  deals,
  startProjectId,
  busy,
  onOpenClients,
  onOpenDeals,
  onOpenChains,
  onOpenContact,
  onConvertLead,
  clientName,
}: Props) {
  const isStart = Boolean(dashboard?.start_inbox);
  const openDeals = Number(dashboard?.open_deals_count ?? overview.deals ?? deals.filter((d) => d.stage !== 'won' && d.stage !== 'lost').length);
  const recent = Array.isArray(dashboard?.recent_deals) && dashboard.recent_deals.length
    ? (dashboard.recent_deals as Row[])
    : deals.slice(0, 8);
  const startLeads = Array.isArray(dashboard?.start_leads) ? (dashboard.start_leads as Row[]) : [];

  return (
    <div className="project-sales-dashboard" data-crm-r7="project-dashboard">
      <div className="metrics sales-metrics" role="group" aria-label="Показатели проекта">
        <button type="button" className="metric-link" onClick={onOpenClients}>
          <p>Клиенты</p>
          <strong>{String(dashboard?.contacts_count ?? overview.contacts ?? 0)}</strong>
          <small>10 / 50 / Все →</small>
        </button>
        <button type="button" className="metric-link" onClick={onOpenDeals}>
          <p>Сделки</p>
          <strong>{String(dashboard?.deals_count ?? overview.deals ?? deals.length)}</strong>
          <small>в работе: {openDeals}</small>
        </button>
        <article>
          <p>В работе</p>
          <strong>{money(overview.pipeline_minor)}</strong>
        </article>
        <article>
          <p>Получено</p>
          <strong>{money(dashboard?.received_minor ?? overview.received_minor)}</strong>
        </article>
      </div>

      <div className="sales-jump row">
        <button type="button" className="primary" onClick={onOpenClients}>
          Клиенты
        </button>
        <button type="button" className="primary" onClick={onOpenDeals}>
          Сделки
        </button>
        <button type="button" onClick={onOpenChains}>
          Конструктор
        </button>
      </div>

      {isStart && (
        <section className="panel start-inbox-panel" aria-label="Входящий интерес Старт">
          <div className="panel-title">
            <h2>Старт · непривязанный интерес</h2>
            <span className="badge">система</span>
          </div>
          <div className="metrics start-metrics">
            <article>
              <p>Открыто</p>
              <strong>{String(dashboard?.start_open_count ?? 0)}</strong>
            </article>
            <article>
              <p>Был контакт</p>
              <strong>{String(dashboard?.start_contacted_count ?? 0)}</strong>
            </article>
            <article>
              <p>В проект</p>
              <strong>{String(dashboard?.start_converted_count ?? 0)}</strong>
            </article>
          </div>
          {!startLeads.length && (
            <p className="muted">Пока нет открытых заявок в «Старт».</p>
          )}
          <ul className="start-lead-list">
            {startLeads.map((lead) => (
              <li key={String(lead.id)}>
                <button type="button" className="name-cell" onClick={() => onOpenContact(String(lead.id))}>
                  <span className="avatar">{String(lead.name || '?').slice(0, 1)}</span>
                  <span>
                    <b>{String(lead.name || 'Без имени')}</b>
                    <small>
                      {lead.contacted ? 'Был контакт' : 'Ещё нет контакта'}
                      {lead.last_action ? ` · ${String(lead.last_action)}` : ''}
                      {lead.service ? ` · ${String(lead.service)}` : ''}
                    </small>
                  </span>
                </button>
                <button
                  type="button"
                  className="link"
                  disabled={busy}
                  onClick={() => onConvertLead(lead)}
                >
                  В проект →
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel" aria-label="Сделки проекта">
        <div className="panel-title">
          <h2>Сделки</h2>
          <button type="button" className="link" onClick={onOpenDeals}>
            Все сделки →
          </button>
        </div>
        {!recent.length && <p className="muted">Сделок пока нет — создайте первую в разделе «Сделки».</p>}
        {recent.map((d) => (
          <button
            key={String(d.id)}
            type="button"
            className="action-row"
            onClick={() => onOpenContact(String(d.profile_id))}
          >
            <span className={`stage-dot ${String(d.stage || 'new')}`} />
            <div>
              <b>{String(d.title)}</b>
              <small>
                {clientName(String(d.profile_id))}
                {startProjectId && d.project_id === startProjectId ? ' · Старт' : ''}
              </small>
            </div>
            <strong>{money(d.amount_minor)}</strong>
            <time>{date(d.due_at)}</time>
          </button>
        ))}
      </section>
    </div>
  );
}
