export function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    bots: 'M12 3a4 4 0 014 4v1h2a3 3 0 010 6h-2v1a4 4 0 01-8 0v-1H6a3 3 0 010-6h2V7a4 4 0 014-4z',
    scenarios: 'M4 6h16 M4 12h10 M4 18h14 M18 10l2 2-2 2',
    broadcasts: 'M5 19L19 5 M5 5h14v14',
    support: 'M4 4h16v12H4z M4 16l8-5 8 5',
    automations: 'M12 3v18 M5 10l7-7 7 7 M8 21h8',
    media: 'M4 6h16v12H4z M8 10l3 3 5-5',
    ai: 'M12 3l2 6h6l-5 4 2 6-5-4-5 4 2-6-5-4h6z',
  };
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={paths[name] || paths.bots} />
    </svg>
  );
}
