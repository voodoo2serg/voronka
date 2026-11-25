'use client';

import { useEffect, useState } from 'react';
import { SIGNAL_MEDIA_LIBRARY_URL, signalMediaLibraryHref } from '../portals';
import type { ApiFn, Row } from '../types';

const DEFAULT_KINDS: { id: string; label: string }[] = [
  { id: 'tg_post', label: 'Пост Telegram' },
  { id: 'tg_circle', label: 'Кружочек TG' },
  { id: 'tg_cover', label: 'Обложка TG' },
  { id: 'vk_video', label: 'VK video' },
  { id: 'email_body', label: 'Письмо' },
  { id: 'poll', label: 'Опрос' },
  { id: 'script', label: 'Скрипт' },
  { id: 'image', label: 'Изображение' },
  { id: 'file', label: 'Файл' },
];

type MediaItem = {
  asset_id: string;
  kind: string;
  project_id?: string;
  title: string;
  preview_url?: string | null;
};

export function MediaLibrarySection({
  api,
  pid,
  busy,
  onBusy,
  onNotice,
  pickMode,
  onPick,
}: {
  api: ApiFn;
  pid: string;
  busy: boolean;
  onBusy: (fn: () => Promise<void>) => Promise<void>;
  onNotice: (v: string) => void;
  pickMode?: boolean;
  onPick?: (assetId: string, item?: MediaItem) => void;
}) {
  const [items, setItems] = useState<MediaItem[]>([]);
  const [kindFilter, setKindFilter] = useState('');
  const [kinds, setKinds] = useState(DEFAULT_KINDS);
  const [fileKinds, setFileKinds] = useState<string[]>(['image', 'tg_cover', 'tg_circle', 'file']);
  const [signalOpenUrl, setSignalOpenUrl] = useState(
    () => signalMediaLibraryHref({ project: pid }) || SIGNAL_MEDIA_LIBRARY_URL,
  );
  const [configured, setConfigured] = useState(true);
  const [contentKind, setContentKind] = useState('tg_post');
  const [title, setTitle] = useState('');
  const [bodyText, setBodyText] = useState('');
  const [file, setFile] = useState<File | null>(null);

  async function refresh() {
    if (!pid) return;
    const qs = kindFilter ? `?content_kind=${encodeURIComponent(kindFilter)}` : '';
    const [list, meta] = await Promise.all([
      api(`/projects/${pid}/signal-media${qs}`),
      api(`/projects/${pid}/signal-media/meta`).catch(() => null),
    ]);
    setItems(Array.isArray(list.items) ? list.items : []);
    setConfigured(list.configured !== false);
    if (list.signal_open_url) setSignalOpenUrl(String(list.signal_open_url));
    if (meta) {
      const labels = (meta.content_kind_labels || {}) as Record<string, string>;
      const ids = (meta.content_kinds as string[]) || Object.keys(labels);
      if (ids.length) {
        setKinds(ids.map((id) => ({ id, label: labels[id] || id })));
      }
      if (Array.isArray(meta.file_required_kinds)) {
        setFileKinds(meta.file_required_kinds.map(String));
      }
      if (meta.signal_open_url) setSignalOpenUrl(String(meta.signal_open_url));
    }
  }

  useEffect(() => {
    void onBusy(refresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, kindFilter]);

  const needsFile = fileKinds.includes(contentKind);

  async function createMeta() {
    await onBusy(async () => {
      const row = await api(`/projects/${pid}/signal-media/create`, 'POST', {
        content_kind: contentKind,
        title: title.trim(),
        body_text: bodyText.trim() || null,
      });
      onNotice(`Создано · ${String(row.title || row.asset_id).slice(0, 40)}`);
      setTitle('');
      setBodyText('');
      await refresh();
      if (pickMode && onPick && row.asset_id) onPick(String(row.asset_id), row as MediaItem);
    });
  }

  async function uploadOrCreate() {
    if (needsFile && file) {
      await onBusy(async () => {
        const fd = new FormData();
        fd.append('content_kind', contentKind);
        if (title.trim()) fd.append('title', title.trim());
        if (bodyText.trim()) fd.append('body_text', bodyText.trim());
        fd.append('file', file);
        const row = await api(`/projects/${pid}/signal-media`, 'POST', fd);
        onNotice(`Загружено · ${String(row.title || row.asset_id).slice(0, 40)}`);
        setFile(null);
        setTitle('');
        await refresh();
        if (pickMode && onPick && row.asset_id) onPick(String(row.asset_id), row as MediaItem);
      });
      return;
    }
    await createMeta();
  }

  if (!pid) {
    return (
      <section className="panel">
        <p className="muted">Выберите проект, чтобы открыть медиатеку.</p>
      </section>
    );
  }

  return (
    <div className="two-column media-library">
      <section className="panel">
        <div className="panel-title">
          <h2>Медиатека</h2>
          <div className="constructor-actions">
            <button type="button" disabled={busy} onClick={() => void onBusy(refresh)}>
              Обновить
            </button>
            <a
              className="constructor-cart-link"
              href={signalOpenUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Открыть в Signal
            </a>
          </div>
        </div>
        <p className="fine">
          Файлы и материалы текущего проекта (Signal SoR). Подключайте к шагам конструктора в CRM.
        </p>
        {!configured && (
          <p className="muted">
            BFF не настроен (SIGNAL_INTERNAL_MEDIA_*). Список и создание недоступны — используйте
            «Открыть в Signal».
          </p>
        )}
        <label>
          Фильтр kind
          <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
            <option value="">Все</option>
            {kinds.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        {items.length === 0 ? (
          <p className="muted" style={{ marginTop: 16 }}>
            Пока пусто — создайте материал справа или в Signal.
          </p>
        ) : (
          <ul className="constructor-list media-library-list">
            {items.map((item) => (
              <li key={item.asset_id}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    if (pickMode && onPick) onPick(item.asset_id, item);
                    else {
                      void navigator.clipboard?.writeText(item.asset_id);
                      onNotice(`asset_id скопирован · ${item.asset_id.slice(0, 8)}…`);
                    }
                  }}
                >
                  <b>{item.title || item.kind}</b>
                  <small>
                    {kinds.find((k) => k.id === item.kind)?.label || item.kind} ·{' '}
                    {item.asset_id.slice(0, 8)}…
                    {pickMode ? ' · выбрать' : ' · копировать id'}
                  </small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel">
        <div className="panel-title">
          <h2>Создать / загрузить</h2>
        </div>
        <label>
          Kind
          <select value={contentKind} onChange={(e) => setContentKind(e.target.value)}>
            {kinds.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Название
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Короткое имя в списке"
          />
        </label>
        {!needsFile && (
          <label>
            Текст / тело
            <textarea
              value={bodyText}
              onChange={(e) => setBodyText(e.target.value)}
              rows={5}
              placeholder="Текст поста, письма, варианты опроса…"
            />
          </label>
        )}
        {needsFile && (
          <label>
            Файл (JPEG/PNG/WebP/GIF)
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </label>
        )}
        <div className="constructor-actions" style={{ marginTop: 12 }}>
          <button
            type="button"
            className="primary"
            disabled={busy || (needsFile && !file && !title.trim())}
            onClick={() => void uploadOrCreate()}
          >
            {needsFile ? 'Загрузить' : 'Создать'}
          </button>
        </div>
        <p className="fine" style={{ marginTop: 12 }}>
          Привязка к Project <code>{pid.slice(0, 8)}…</code> обязательна (Signal CART-02).
        </p>
      </section>
    </div>
  );
}

export type { MediaItem };
