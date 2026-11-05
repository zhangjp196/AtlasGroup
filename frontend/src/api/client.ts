export const API_PREFIX = '/api/agent-zero'

export class ApiError extends Error {
  status: number
  constructor(message: string, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

type RequestOptions = Omit<RequestInit, 'body'> & { body?: unknown }

export async function api<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, headers, ...rest } = options
  const init: RequestInit = { ...rest }
  if (headers) init.headers = headers
  if (body !== undefined) {
    if (body instanceof FormData) {
      init.body = body
    } else {
      init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) }
      init.body = JSON.stringify(body)
    }
  }
  const res = await fetch(API_PREFIX + path, init)
  let payload: unknown = null
  try {
    payload = await res.json()
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload
      ? String((payload as { detail: unknown }).detail)
      : `HTTP ${res.status}`
    throw new ApiError(detail, res.status)
  }
  if (payload && typeof payload === 'object' && 'data' in payload) {
    return (payload as { data: T }).data
  }
  return payload as T
}

export function apiUrl(path: string): string {
  return API_PREFIX + path
}

export function sseUrl(path: string): string {
  return API_PREFIX + path
}
