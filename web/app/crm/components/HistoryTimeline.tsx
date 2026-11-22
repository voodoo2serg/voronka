'use client';

import type { Row } from '../types';
import { date, money } from '../utils';

type HistoryItem = {
  key: string;
  at: string | number | null | undefined;
  stage: 'pay' | 'mail' | 'access' | 'course' | 'touch' | 'other';
  stageLabel: string;
  title: string;
  body: string;
  meta: string;
  letter?: boolean;
};

function stageFromInteraction(ix: Row): Pick<HistoryItem, 'stage' | 'stageLabel' | 'title' | 'letter'> {
  const kind = String(ix.kind || '');
  const channel = String(ix.channel || '');
  const payload = (ix.payload || {}) as Row;
  if (kind === 'deal.paid' || channel === 'payment' || payload.stage === 'pay') {
    const sandbox = Boolean(payload.sandbox);
    return {
      stage: 'pay',
      stageLabel: sandbox ? 'Оплата · sandbox' : 'Оплата',
      title: sandbox ? 'Sandbox-оплата зафиксирована' : 'Оплата зафиксирована',
      letter: false,
    };
  }
  if (
    kind === 'learning.completed' ||
    kind === 'learning.progress' ||
    payload.stage === 'course' ||
    channel === 'lms_end'
  ) {
    const percent = typeof payload.progress_percent === 'number' ? payload.progress_percent : null;
    const done = Boolean(payload.completed) || kind === 'learning.completed';
    const summary = payload.summary ? String(payload.summary) : '';
    return {
      stage: 'course',
      stageLabel: 'Курс',
      title: done
        ? summary
          ? `Курс завершён · ${summary}`
          : 'Курс завершён'
        : percent != null
          ? `Прогресс · ${percent}%`
          : summary
            ? `Прогресс · ${summary}`
            : 'Прогресс по курсу',
      letter: false,
    };
  }
  if (kind === 'course.access_granted' || kind.startsWith('access.') || payload.stage === 'access') {
    return {
      stage: 'access',
      stageLabel: 'Открыть курс',
      title: 'Доступ открыт · можно открыть курс',
      letter: false,
    };
  }
  if (
    channel === 'email' ||
    kind.startsWith('email.') ||
    kind.includes('mail') ||
    kind.includes('send') ||
    payload.stage === 'mail'
  ) {
    const status = payload.status ? String(payload.status) : '';
    return {
      stage: 'mail',
      stageLabel: 'Письмо',
      title: status ? `Письмо · ${status}` : 'Письмо',
      letter: true,
    };
  }
  return {
    stage: 'touch',
    stageLabel: 'Касание',
    title: kind || 'Событие',
    letter: false,
  };
}

/** FUNC-M2: operator-visible History — sandbox pay / mail as stages & letters (not API-only). */
export function HistoryTimeline({ detail }: { detail: Row }) {
  const items: HistoryItem[] = [];

  for (const ix of (detail.person_interactions || []) as Row[]) {
    const painted = stageFromInteraction(ix);
    const comm = ((detail.person_communications || []) as Row[]).find((c) => c.id === ix.communication_id);
    const preview =
      (comm && String(comm.body_preview || '')) ||
      (ix.payload?.evidence ? String(ix.payload.evidence) : '') ||
      String(ix.external_event_key || '');
    const amount =
      typeof ix.payload?.amount_minor === 'number' ? ` · ${money(ix.payload.amount_minor)}` : '';
    items.push({
      key: 'ix-' + String(ix.id),
      at: ix.occurred_at || ix.created_at,
      ...painted,
      title: painted.title + amount,
      body: preview,
      meta: `${ix.source_service || 'voronka'} · ${ix.channel || painted.stage} · ${ix.direction || 'system'}`,
    });
  }

  // Orphan communications without interaction row still show as letters.
  const linked = new Set(
    ((detail.person_interactions || []) as Row[])
      .map((ix) => ix.communication_id)
      .filter(Boolean)
      .map(String),
  );
  for (const c of (detail.person_communications || []) as Row[]) {
    if (linked.has(String(c.id))) continue;
    items.push({
      key: 'comm-' + String(c.id),
      at: c.created_at || c.updated_at,
      stage: 'mail',
      stageLabel: 'Письмо',
      title: `Письмо · ${c.status || 'queued'}`,
      body: String(c.body_preview || ''),
      meta: `${c.channel || 'email'} · ${c.purpose || 'transactional'}`,
      letter: true,
    });
  }

  for (const a of (detail.activities || []) as Row[]) {
    const outcome = String(a.outcome || '');
    const isLearning =
      a.channel === 'lms_end' ||
      outcome.startsWith('learning.') ||
      a.kind === 'learning';
    items.push({
      key: 'act-' + String(a.id),
      at: a.created_at,
      stage: a.channel === 'email' ? 'mail' : isLearning ? 'course' : 'touch',
      stageLabel: a.channel === 'email' ? 'Письмо' : isLearning ? 'Курс' : 'Касание',
      title: String(
        a.outcome || (a.kind === 'ai_draft' ? 'Черновик ИИ' : isLearning ? 'Обучение' : 'Контакт'),
      ),
      body: String(a.body || ''),
      meta: `${a.channel || 'manual'}${a.kind === 'ai_draft' ? ' · Черновик ИИ' : ''}`,
      letter: a.channel === 'email',
    });
  }

  for (const e of (detail.events || []) as Row[]) {
    items.push({
      key: 'ev-' + String(e.id),
      at: e.created_at,
      stage: 'other',
      stageLabel: 'Событие',
      title: String(e.kind || 'event'),
      body: String(e.payload?.text || e.payload?.answer || ''),
      meta: 'event',
    });
  }

  items.sort((a, b) => {
    const ta = a.at ? new Date(String(a.at)).getTime() : 0;
    const tb = b.at ? new Date(String(b.at)).getTime() : 0;
    return tb - ta;
  });

  if (!items.length) {
    return <p className="fine">История пуста — sandbox-оплата и письма появятся здесь как стадии.</p>;
  }

  return (
    <div className="history-timeline" aria-label="История клиента">
      {items.map((item) => (
        <article
          className={`timeline history-item stage-${item.stage}${item.letter ? ' is-letter' : ''}`}
          key={item.key}
        >
          <div className="history-head">
            <span className={`history-stage stage-${item.stage}`}>{item.stageLabel}</span>
            <small>
              {date(item.at == null ? undefined : String(item.at))} · {item.meta}
            </small>
          </div>
          <b>{item.title}</b>
          {item.body ? <p>{item.body}</p> : null}
        </article>
      ))}
    </div>
  );
}
