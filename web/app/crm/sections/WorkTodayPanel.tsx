'use client';

import { CONTOUR_STAGES, lmsOpenCourseHref, signalPersonLinks } from '../portals';
import type { Row } from '../types';

type Props = {
  data: Row | null;
  busy?: boolean;
  onOpenPerson: (profileId: string) => void;
  onRefresh?: () => void;
};

/** FUNC-L1: one sales contour lead → touch → pay → access → course → mail. */
export function WorkTodayPanel({ data, busy, onOpenPerson, onRefresh }: Props) {
  const items = Array.isArray(data?.items) ? (data.items as Row[]) : [];
  const labels = (data?.labels || {}) as Row;
  const stages = Array.isArray(data?.contour)
    ? (data.contour as string[])
    : CONTOUR_STAGES.map((s) => s.id);

  return (
    <section className="panel work-today-panel" id="work-today-contour" aria-label="Работа сегодня · контур продаж">
      <div className="panel-title">
        <h2>Работа сегодня</h2>
        <div className="row" style={{ gap: 8 }}>
          {onRefresh && (
            <button type="button" disabled={busy} onClick={onRefresh}>
              Обновить
            </button>
          )}
          <span className="badge">{items.length ? `${items.length} в очереди` : 'очередь'}</span>
        </div>
      </div>
      <p className="muted">
        Один контур статусов: лид → касание → оплата → доступ → курс → письмо. Deep-link в Person / SIGNAL / LMS,
        когда данные есть. Live-деньги и production-маркетинг из UI не включаются.
      </p>
      <ol className="contour-legend" aria-label="Этапы контура">
        {stages.map((id) => (
          <li key={id}>
            <span className="contour-dot empty" />
            {String(labels[id] || CONTOUR_STAGES.find((s) => s.id === id)?.label || id)}
          </li>
        ))}
      </ol>
      {!data && <p className="fine">Загрузка контура…</p>}
      {data && !items.length && (
        <div className="empty compact">
          Нет клиентов в очереди. Открытые сделки, задачи и заказы появятся здесь одним контуром.
        </div>
      )}
      {items.map((row) => {
        const statuses = (row.statuses || {}) as Row;
        const links = (row.links || {}) as Row;
        const personLinks = signalPersonLinks(typeof row.person_id === 'string' ? row.person_id : null);
        return (
          <article className="contour-row" key={String(row.profile_id)}>
            <button
              type="button"
              className="contour-person"
              onClick={() => onOpenPerson(String(row.profile_id))}
            >
              <b>{String(row.name || 'Клиент')}</b>
              <small>
                {row.deal_title
                  ? `${row.deal_title}${row.deal_stage ? ` · ${row.deal_stage}` : ''}`
                  : row.open_task_title
                    ? String(row.open_task_title)
                    : 'Без открытой сделки'}
                {row.attention ? ` · дальше: ${String(labels[String(row.attention)] || row.attention)}` : ''}
              </small>
            </button>
            <div className="contour-stages" role="list" aria-label="Статусы цикла">
              {stages.map((id) => {
                const st = (statuses[id] || {}) as Row;
                const done = Boolean(st.done);
                const href = typeof st.href === 'string' ? st.href : null;
                const cls = done ? 'done' : id === row.attention ? 'active' : 'empty';
                if (href && (id === 'lms' || id === 'course' || id === 'access' || id === 'mail')) {
                  // External deep-links when present; person stages open drawer via row click.
                  if ((id === 'course' || id === 'access') && href.includes('lms.')) {
                    return (
                      <a
                        key={id}
                        className={`contour-chip ${cls}`}
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        title={String(st.summary || id)}
                        role="listitem"
                      >
                        {String(labels[id] || id)}
                      </a>
                    );
                  }
                  if (id === 'mail' && href.includes('signal')) {
                    return (
                      <a
                        key={id}
                        className={`contour-chip ${cls}`}
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        title={String(st.summary || id)}
                        role="listitem"
                      >
                        {String(labels[id] || id)}
                      </a>
                    );
                  }
                }
                return (
                  <button
                    key={id}
                    type="button"
                    className={`contour-chip ${cls}`}
                    title={String(st.summary || id)}
                    role="listitem"
                    onClick={() => onOpenPerson(String(row.profile_id))}
                  >
                    {String(labels[id] || id)}
                  </button>
                );
              })}
            </div>
            <div className="contour-links" aria-label="Deep-links">
              <button type="button" className="link" onClick={() => onOpenPerson(String(row.profile_id))}>
                Person
              </button>
              {personLinks.hasPerson ? (
                <>
                  <a href={personLinks.ce} target="_blank" rel="noreferrer">
                    SIGNAL CE
                  </a>
                  <a href={personLinks.sets} target="_blank" rel="noreferrer">
                    Набор
                  </a>
                </>
              ) : (
                <span className="fine">нет Person → SIGNAL</span>
              )}
              <a href={typeof links.lms === 'string' ? String(links.lms) : lmsOpenCourseHref()} target="_blank" rel="noreferrer">
                Открыть курс
              </a>
            </div>
          </article>
        );
      })}
      <p className="fine">
        Q6: sandbox only — UI не выставляет <code>ROBOKASSA_LIVE_READY</code> и не включает production marketing.
      </p>
    </section>
  );
}
