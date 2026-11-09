export type Row = Record<string, any>;
export type Field = { key: string; label: string; type?: string; value?: string; options?: [string, string][]; required?: boolean };
export type FormConfig = { title: string; fields: Field[]; save: (data: Record<string, string>) => Promise<void> };
export type ApiFn = (path: string, method?: string, body?: unknown, raw?: boolean) => Promise<any>;
