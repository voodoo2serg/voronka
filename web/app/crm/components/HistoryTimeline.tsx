'use client';

import { useCallback, useEffect, useState } from 'react';
import type { Row } from '../types';
import { date, money } from '../utils';

type HistoryItem = {
  key: string;
  at: string | number | null | undefined;
  stage: 'pay' | 'mail' | 'access' | 'course' | 'touch' | 'other' | 'tech' | 'note';
  stageLabel: string;
  title: string;
  body: string;
  meta: string;
  letter?: boolean;
  category?: string;
  projectLabel?: string;
};

type Filter = 'all' | 'correspondence' | 'technical';

type ApiFn = (path: string, method?: string, body?: unknown) => Promise<any>;

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

function stageFromApiRow(row: Row): HistoryItem {
  const kind = String(row.kind || '');
  const category = String(row.category || '');
  const channel = String(row.channel || '');
  const ref = (row.payload_ref || {}) as Row;
  let stage: HistoryItem['stage'] = category === 'technical' ? 'tech' : 'touch';
  let stageLabel = category === 'technical' ? 'Техника' : 'Переписка';
  let letter = false;
  if (kind === 'deal.paid' || ref.sandbox != null) {
    stage = 'pay';
    stageLabel = ref.sandbox ? 'Оплата · sandbox' : 'Оплата';
  } else if (kind.startsWith('access.') || kind === 'course.access_granted') {
    stage = 'access';
    stageLabel = 'Доступ';
  } else if (kind.startsWith('learning.') || kind === 'course.enter' || kind === 'module.open') {
    stage = 'course';
    stageLabel = 'Курс';
  } else if (channel === 'email' || kind.startsWith('email.') || kind.startsWith('message.')) {
    stage = 'mail';
    stageLabel = channel === 'email' ? 'Письмо' : 'Сообщение';
    letter = channel === 'email' || kind.startsWith('email.');
  } else if (kind === 'staff.note') {
    stage = 'note';
    stageLabel = 'Заметка';
  } else if (kind.startsWith('consent.') || kind === 'outbox.failed') {
    stage = 'tech';
    stageLabel = kind.startsWith('consent.') ? 'Согласие' : 'Доставка';
  } else if (kind.includes('channel_invite')) {
    stage = 'touch';
    stageLabel = 'Приглашение';
  }
  const ctx = [row.project_name, row.branch_name].filter(Boolean).join(' · ');
  const amount = typeof ref.amount_minor === 'number' ? ` · ${money(ref.amount_minor)}` : '';
  return {
    key: String(row.id),
    at: row.occurred_at,
    stage,
    stageLabel,
    title: String(row.title || kind) + amount,
    body: String(row.summary || ''),
    meta: `${row.source || 'voronka'} · ${channel || stage}${ctx ? ` · ${ctx}` : ''}`,
    letter,
    category,
    projectLabel: ctx,
  };
}

function buildLocalItems(detail: Row): HistoryItem[] {
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
      category: painted.stage === 'pay' || painted.stage === 'access' || painted.stage === 'course' ? 'technical' : 'correspondence',
    });
  }

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
      category: 'correspondence',
    });
  }

  for (const a of (detail.activities || []) as Row[]) {
    const outcome = String(a.outcome || '');
    const isLearning =
      a.channel === 'lms_end' || outcome.startsWith('learning.') || a.kind === 'learning';
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
      category: isLearning ? 'technical' : 'correspondence',
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
      category: String(e.kind || '').includes('channel_invite') ? 'correspondence' : 'technical',
    });
  }

  items.sort((a, b) => {
    const ta = a.at ? new Date(String(a.at)).getTime() : 0;
    const tb = b.at ? new Date(String(b.at)).getTime() : 0;
    return tb - ta;
  });
  return items;
}

const PAGE = 20;
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'Всё' },
  { id: 'correspondence', label: 'Переписка' },
  { id: 'technical', label: 'Техника' },
];

/** VR-452 / FUNC-M2: Person История — filters + pagination when Person linked. */
export function HistoryTimeline({
  detail,
  api,
  projectId,
}: {
  detail: Row;
  api?: ApiFn;
  projectId?: string;
}) {
  const personId = typeof detail.person_id === 'string' ? detail.person_id : null;
  const useApi = Boolean(api && projectId && personId);

  const [filter, setFilter] = useState<Filter>('all');
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!useApi || !api || !projectId || !personId) {
      const local = buildLocalItems(detail);
      const filtered =
        filter === 'all' ? local : local.filter((i) => i.category === filter);
      setTotal(filtered.length);
      setItems(filtered.slice(offset, offset + PAGE));
      return;
    }
    setLoading(true);
    setError('');
    try {
      const q = new URLSearchParams({
        category: filter,
        offset: String(offset),
        limit: String(PAGE),
      });
      const data = await api(`/projects/${projectId}/persons/${personId}/timeline?${q}`);
      setTotal(Number(data.total || 0));
      setItems(((data.items || []) as Row[]).map(stageFromApiRow));
    } catch (e: any) {
      setError(e?.message || 'Не удалось загрузить историю');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [api, detail, filter, offset, personId, projectId, useApi]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setOffset(0);
  }, [filter, personId]);

  return (
    <div className="history-timeline" aria-label="История клиента">
      <div className="history-filters" role="tablist" aria-label="Фильтр истории">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            role="tab"
            aria-selected={filter === f.id}
            className={filter === f.id ? 'is-active' : undefined}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>
      {loading && <p className="fine">Загрузка…</p>}
      {error && <p className="fine error">{error}</p>}
      {!loading && !error && !items.length && (
        <p className="fine">История пуста — sandbox-оплата и письма появятся здесь как стадии.</p>
      )}
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
      {total > PAGE && (
        <div className="row history-pager">
          <button type="button" disabled={offset <= 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            Назад
          </button>
          <span className="fine">
            {offset + 1}–{Math.min(offset + PAGE, total)} из {total}
          </span>
          <button
            type="button"
            disabled={offset + PAGE >= total || loading}
            onClick={() => setOffset(offset + PAGE)}
          >
            Дальше
          </button>
        </div>
      )}
    </div>
  );
}
