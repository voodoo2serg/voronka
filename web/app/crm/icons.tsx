export function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    contacts: 'M8 3h8 M5 7h14v14H5z M9 11h6 M9 15h6',
    inbox: 'M4 4h16v12H4z M4 16l8-5 8 5',
    deals: 'M3 4h5v16H3z M10 4h5v10h-5z M17 4h4v13h-4z',
    products: 'M12 3l9 9-9 9-9-9z',
    tasks: 'M5 12l4 4L20 5',
    orders: 'M8 21V3h6a5 5 0 010 10H5 M5 17h11',
    campaigns: 'M5 19L19 5 M5 5h14v14',
    outreach: 'M12 3v18 M5 10l7-7 7 7 M8 21h8',
    imports: 'M7 3v17 M3 16l4 4 4-4 M17 21V4 M13 8l4-4 4 4',
    settings: 'M12 8a4 4 0 100 8 4 4 0 000-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
  };
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={paths[name] || paths.overview} />
    </svg>
  );
}
