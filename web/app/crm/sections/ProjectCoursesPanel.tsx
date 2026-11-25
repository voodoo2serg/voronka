'use client';

import type { Row } from '../types';

type Props = {
  courses: Row[];
  projectName?: string;
  busy: boolean;
  canEdit: boolean;
  onBind: () => void;
  onUnbind: (courseId: string) => void;
};

function siblingNames(row: Row, currentProjectId?: string): string[] {
  const projects = Array.isArray(row.projects) ? (row.projects as Row[]) : [];
  return projects
    .filter((p) => !currentProjectId || String(p.project_id) !== currentProjectId)
    .map((p) => String(p.project_name || p.project_id || ''))
    .filter(Boolean);
}

/** CRM-R11: Course ↔ many Projects; staff see Project name on course bindings. */
export function ProjectCoursesPanel({
  courses,
  projectName,
  busy,
  canEdit,
  onBind,
  onUnbind,
}: Props) {
  return (
    <section className="panel" style={{ marginTop: 16 }} data-crm-r11="project-courses" aria-label="Курсы LMS">
      <div className="panel-title">
        <div>
          <h2>Курсы LMS</h2>
          <p className="muted" style={{ margin: '4px 0 0' }}>
            Один курс может быть в нескольких проектах
            {projectName ? (
              <>
                {' '}
                · этот: <b>{projectName}</b>
              </>
            ) : null}
            .
          </p>
        </div>
        {canEdit && (
          <button type="button" className="link" disabled={busy} onClick={onBind}>
            + Курс
          </button>
        )}
      </div>
      {!courses.length && (
        <p className="muted">Пока нет привязок. Добавьте LMS course_id — тот же курс можно повесить на другой проект.</p>
      )}
      <ul className="branch-list course-bind-list">
        {courses.map((c) => {
          const courseId = String(c.course_id || '');
          const siblings = siblingNames(c, typeof c.project_id === 'string' ? c.project_id : undefined);
          const ownName = String(c.project_name || projectName || '');
          return (
            <li key={String(c.id || courseId)}>
              <div className="row" style={{ alignItems: 'flex-start', gap: 12 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div>
                    <code>{courseId}</code>
                    {c.label ? <span> {String(c.label)}</span> : null}
                  </div>
                  {ownName ? (
                    <p className="fine" style={{ margin: '4px 0 0' }}>
                      Проект: <b>{ownName}</b>
                    </p>
                  ) : null}
                  {siblings.length > 0 ? (
                    <p className="fine muted" style={{ margin: '2px 0 0' }}>
                      Также в: {siblings.join(', ')}
                    </p>
                  ) : (
                    <p className="fine muted" style={{ margin: '2px 0 0' }}>
                      Только в этом проекте
                    </p>
                  )}
                </div>
                {canEdit && (
                  <button
                    type="button"
                    className="link"
                    disabled={busy || !courseId}
                    onClick={() => onUnbind(courseId)}
                  >
                    Отвязать
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
