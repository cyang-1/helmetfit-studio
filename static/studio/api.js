export async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { cache: 'no-store', ...options });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw Error('暂时无法连接工作台，请稍后重试。');
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw Error('工作台暂未返回有效结果，请刷新后重试。');
  }
  if (!response.ok) {
    const error = Error(data.error || data.detail || data.message || '操作失败，请重试。');
    error.status = response.status;
    throw error;
  }
  return data;
}
export const sendJSON = (path, data = {}) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
export function message(element, value) {
  element.textContent = value;
  element.hidden = !value;
}
export function uuid() {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) =>
        n.toString(16).padStart(2, '0')
      ).join('');
}
export function artifactURL(value) {
  if (typeof value !== 'string' || !value) return null;
  const url = new URL(value, location.origin);
  return url.origin === location.origin && ['http:', 'https:'].includes(url.protocol) ? url.href : null;
}
