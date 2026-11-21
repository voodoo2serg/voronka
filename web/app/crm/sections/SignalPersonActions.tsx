'use client';

import { signalPersonLinks } from '../portals';

/** FUNC-L3: open Signal CE / message-set from Person without copying UUID. */
export function SignalPersonActions({
  personId,
  compact = false,
}: {
  personId: string | null | undefined;
  compact?: boolean;
}) {
  const links = signalPersonLinks(personId);
  if (!personId && !compact) {
    return (
      <p className="fine signal-person-actions">
        Person ещё не связан — SIGNAL CE / набор откроются без привязки клиента.
        <span className="row" style={{ marginTop: 8, gap: 10 }}>
          <a href={links.ce} target="_blank" rel="noreferrer">
            SIGNAL · CE / регламенты ↗
          </a>
          <a href={links.sets} target="_blank" rel="noreferrer">
            SIGNAL · Набор сообщений ↗
          </a>
        </span>
      </p>
    );
  }
  return (
    <div className={`signal-person-actions${compact ? ' compact' : ''}`} aria-label="SIGNAL без копирования UUID">
      {!compact && <h3>SIGNAL</h3>}
      {!compact && (
        <p className="fine">
          Открыть CE или набор сообщений с Person в ссылке — UUID копировать не нужно.
        </p>
      )}
      <div className="row signal-launch-row">
        <a className={compact ? 'link' : 'primary'} href={links.ce} target="_blank" rel="noreferrer">
          {compact ? 'SIGNAL CE' : 'Открыть CE / регламенты ↗'}
        </a>
        <a href={links.sets} target="_blank" rel="noreferrer">
          {compact ? 'Набор' : 'Набор сообщений ↗'}
        </a>
      </div>
    </div>
  );
}
