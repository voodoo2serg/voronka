import type { Row } from './types';

/** Typed CRM API client. Prefers HttpOnly cookie session; Bearer kept for in-memory token after login. */
export function createCrmApi(getToken: () => string, onUnauthorized: () => void) {
  return async function api(path: string, method = 'GET', body?: unknown, raw = false) {
    const token = getToken();
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = 'Bearer ' + token;
    if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
    const response = await fetch(path.startsWith('/api/') ? path : '/api/crm' + path, {
      method,
      headers,
      credentials: 'include',
      body: body ? (body instanceof FormData ? body : JSON.stringify(body)) : undefined,
    });
    if (!response.ok) {
      let message = 'Не удалось выполнить действие';
      try {
        const data = await response.json();
        message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      } catch {}
      if (response.status === 401 && !path.includes('/login')) {
        sessionStorage.removeItem('crm-token');
        onUnauthorized();
      }
      throw new Error(message);
    }
    return raw ? response.blob() : response.json();
  };
}

export type Me = Row;
