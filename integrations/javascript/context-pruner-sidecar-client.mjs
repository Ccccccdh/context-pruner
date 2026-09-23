/**
 * Zero-dependency JavaScript client for the Context-Pruner lifecycle Sidecar.
 *
 * The host remains the owner of the full conversation. `beforeModel()` returns
 * a temporary model view and must never replace the host's authoritative history.
 */

export class ContextPrunerSidecarError extends Error {
  constructor(message, { status = 0, code = '', details = null } = {}) {
    super(message);
    this.name = 'ContextPrunerSidecarError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class ContextPrunerSidecarClient {
  constructor({ baseUrl, authToken = '', timeoutMs = 30_000, fetchImpl = globalThis.fetch } = {}) {
    if (typeof fetchImpl !== 'function') {
      throw new TypeError('A WHATWG-compatible fetch implementation is required');
    }
    const parsed = new URL(String(baseUrl || ''));
    if (!['http:', 'https:'].includes(parsed.protocol)) {
      throw new TypeError('baseUrl must use http or https');
    }
    this.baseUrl = parsed.toString().replace(/\/$/, '');
    this.authToken = String(authToken || '');
    this.timeoutMs = Math.max(1, Number(timeoutMs) || 30_000);
    this.fetchImpl = fetchImpl;
  }

  async health() {
    return this.#request('GET', '/health', undefined, false);
  }

  async capabilities() {
    return this.#request('GET', '/v1/capabilities', undefined, true);
  }

  async beforeModel(sessionId, payload) {
    return this.#sessionRequest(sessionId, 'before-model', 'POST', payload);
  }

  async afterModel(sessionId, output) {
    return this.#sessionRequest(sessionId, 'after-model', 'POST', { output });
  }

  async afterTool(sessionId, output) {
    return this.#sessionRequest(sessionId, 'after-tool', 'POST', { output });
  }

  async onError(sessionId, error, { query = '', recover = true } = {}) {
    return this.#sessionRequest(sessionId, 'error', 'POST', {
      error: String(error),
      query: String(query),
      recover: Boolean(recover),
    });
  }

  async finalize(sessionId, metadata = {}) {
    return this.#sessionRequest(sessionId, 'finalize', 'POST', { metadata });
  }

  async getState(sessionId) {
    return this.#sessionRequest(sessionId, 'state', 'GET');
  }

  async restoreState(sessionId, lifecycleState, { taskState = '', ...config } = {}) {
    return this.#sessionRequest(sessionId, 'state', 'PUT', {
      lifecycle_state: lifecycleState,
      task_state: String(taskState),
      ...config,
    });
  }

  async deleteSession(sessionId) {
    return this.#sessionRequest(sessionId, '', 'DELETE');
  }

  async #sessionRequest(sessionId, suffix, method, payload) {
    const normalized = String(sessionId || '').trim();
    if (!normalized) {
      throw new TypeError('sessionId is required');
    }
    const tail = suffix ? `/${suffix}` : '';
    return this.#request(
      method,
      `/v1/sessions/${encodeURIComponent(normalized)}${tail}`,
      payload,
      true,
    );
  }

  async #request(method, path, payload, authenticated) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    const headers = { Accept: 'application/json' };
    if (payload !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    if (authenticated && this.authToken) {
      headers.Authorization = `Bearer ${this.authToken}`;
    }
    try {
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: payload === undefined ? undefined : JSON.stringify(payload),
        signal: controller.signal,
      });
      const text = await response.text();
      let data = {};
      if (text) {
        try {
          data = JSON.parse(text);
        } catch (error) {
          throw new ContextPrunerSidecarError('Sidecar returned invalid JSON', {
            status: response.status,
            details: text.slice(0, 300),
          });
        }
      }
      if (!response.ok) {
        const remote = data?.error || {};
        throw new ContextPrunerSidecarError(
          String(remote.message || `Sidecar request failed with HTTP ${response.status}`),
          {
            status: response.status,
            code: String(remote.code || ''),
            details: data,
          },
        );
      }
      return data;
    } catch (error) {
      if (error instanceof ContextPrunerSidecarError) {
        throw error;
      }
      const timedOut = error?.name === 'AbortError';
      throw new ContextPrunerSidecarError(
        timedOut ? `Sidecar request timed out after ${this.timeoutMs} ms` : String(error?.message || error),
        { code: timedOut ? 'timeout' : 'transport_error' },
      );
    } finally {
      clearTimeout(timeout);
    }
  }
}
