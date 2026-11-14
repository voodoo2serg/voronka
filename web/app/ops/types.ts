export type Row = Record<string, any>;
export type ApiFn = (path: string, method?: string, body?: unknown, raw?: boolean) => Promise<any>;
