/** Ops API client. Prefers HttpOnly crm_session cookie; Bearer after stub login. */

export function createOpsApi(getToken: () => string, onUnauthorized: () => void) {
  return async function api(path: string, method = 'GET', body?: unknown, raw = false) {
    const token = getToken();
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = 'Bearer ' + token;
    if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
    const url = path.startsWith('/api/') ? path : '/api/ops' + (path.startsWith('/') ? path : '/' + path);
    const response = await fetch(url, {
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
        onUnauthorized();
      }
      throw new Error(message);
    }
    return raw ? response.blob() : response.json();
  };
}
