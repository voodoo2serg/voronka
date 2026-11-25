'use client';

import type { Row } from '../types';
import { money } from '../utils';

export function ProjectsBoardSection({
  items,
  busy,
  onOpen,
  onCreate,
}: {
  items: Row[];
  busy: boolean;
  onOpen: (id: string) => void;
  onCreate: () => void;
}) {
  return (
    <div className="projects-board" data-crm-home="projects-board" aria-label="Доска проектов">
      <div className="panel-title" style={{ marginBottom: 18 }}>
        <div>
          <h2>Проекты</h2>
          <p className="muted">Первый экран после входа: контакты, деньги и ближайшие задачи по каждому направлению.</p>
        </div>
        <button type="button" className="primary" disabled={busy} onClick={onCreate}>
          + Проект
        </button>
      </div>
      {!items.length && (
        <section className="panel">
          <p className="muted">Пока нет проектов. Создайте первый или дождитесь системного «Старт».</p>
        </section>
      )}
      <div className="project-cards">
        {items.map((p) => {
          const tasks = (p.next_tasks as Row[] | undefined) || [];
          return (
            <button
              type="button"
              key={String(p.id)}
              className={`project-card${p.is_system ? ' system' : ''}`}
              onClick={() => onOpen(String(p.id))}
            >
              <div className="project-card-head">
                <h3>{String(p.name)}</h3>
                {p.is_system && <span className="badge">система</span>}
              </div>
              <p className="muted">{String(p.description || p.goal || '—')}</p>
              <div className="metrics compact-metrics">
                <article>
                  <p>Контакты</p>
                  <strong>{String(p.contacts_count || 0)}</strong>
                </article>
                <article>
                  <p>Деньги</p>
                  <strong>{money(p.received_minor)}</strong>
                </article>
                <article>
                  <p>Ветки</p>
                  <strong>{String(p.branches_count || 0)}</strong>
                </article>
              </div>
              <div className="project-next">
                <b>Ближайшие задачи</b>
                {tasks.length ? (
                  <ul>
                    {tasks.slice(0, 3).map((t) => (
                      <li key={String(t.id)}>{String(t.title)}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="fine">Нет срочных задач</p>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
