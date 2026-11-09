import { artifactURL, uuid } from './api.js';
import { originalChoice, recordedStructure } from './wear-structure.js';
import { views } from './upload-views.js';

const modes = ['single', 'multi', 'text', 'model'];
const prefix = 'helmetfit:composer:v1:';
const cache = new Map();
const localFiles = new Map();
let database;

function emptyDraft(key) {
  return {
    key,
    mode: 'single',
    names: {},
    ids: {},
    inputs: {},
    text: { theme: '', preset: 'character', structure: 'auto' },
    modelStructure: 'full_shell',
    extraOpen: false,
    exampleId: uuid()
  };
}
function projectDraft(project) {
  const draft = emptyDraft(project.id);
  draft.mode = project.source_type === 'text' ? 'text' : project.input_mode || 'model';
  draft.names[draft.mode] = project.name || '';
  draft.modelStructure = recordedStructure(project);
  if (project.prompt_spec) {
    draft.text = {
      theme: project.prompt_spec.theme || '',
      preset: project.prompt_spec.preset || 'character',
      structure: originalChoice(project)
    };
  }
  (project.reference_views || []).forEach((ref, index) => {
    const role =
      draft.mode === 'single'
        ? 'reference'
        : views.some(([k]) => k === ref.role)
          ? ref.role
          : 'unassigned-' + index;
    draft.inputs[role] = { name: ref.filename || '参考图 ' + (index + 1), url: ref.url };
  });
  if (project.source_file?.url)
    draft.inputs.model = {
      name: project.source_file.filename || '原始模型',
      url: project.source_file.url
    };
  else if (draft.mode === 'model') draft.missingModel = true;
  draft.extraOpen = views.slice(4).some(([key]) => draft.inputs[key]);
  return draft;
}
export function draftFor(key, project) {
  if (cache.has(key)) return cache.get(key);
  try {
    const saved = JSON.parse(localStorage.getItem(prefix + key));
    if (saved?.key === key && modes.includes(saved.mode) && saved.inputs && saved.ids && saved.names) {
      cache.set(key, saved);
      return saved;
    }
  } catch {}
  if (key !== 'new' && !project) return null;
  const draft = project ? projectDraft(project) : emptyDraft(key);
  cache.set(key, draft);
  return draft;
}
export function saveDraft(draft) {
  cache.set(draft.key, draft);
  try {
    localStorage.setItem(prefix + draft.key, JSON.stringify(draft));
  } catch {}
}
export function requestId(draft, mode) {
  if (!draft.ids[mode]) {
    draft.ids[mode] = uuid();
    saveDraft(draft);
  }
  return draft.ids[mode];
}
function openDatabase() {
  if (!database)
    database = new Promise((resolve, reject) => {
      const request = indexedDB.open('helmetfit-composer-files', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('files');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  return database;
}
async function storage(action, key, file) {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('files', action === 'get' ? 'readonly' : 'readwrite');
    const store = tx.objectStore('files');
    const req = action === 'put' ? store.put(file, key) : store[action](key);
    tx.oncomplete = () => resolve(req.result);
    tx.onabort = tx.onerror = () => reject(tx.error || Error('浏览器文件缓存不可用。'));
  });
}
export function localInput(file) {
  const key = uuid();
  localFiles.set(key, file);
  return {
    name: file.name,
    type: file.type,
    size: file.size,
    lastModified: file.lastModified,
    localKey: key
  };
}
export async function retainLocal(input) {
  if (!input.localKey) return;
  await storage('put', input.localKey, localFiles.get(input.localKey));
  localFiles.delete(input.localKey);
}
export function releaseLocal(input) {
  if (!input?.localKey) return;
  localFiles.delete(input.localKey);
  storage('delete', input.localKey).catch(() => {});
}
export function discardDraft(key) {
  const draft = draftFor(key, null);
  for (const input of Object.values(draft?.inputs || {})) releaseLocal(input);
  cache.delete(key);
  try {
    localStorage.removeItem(prefix + key);
  } catch {}
}
export async function getLocal(input) {
  const file =
    localFiles.get(input.localKey) || (await storage('get', input.localKey).catch(() => null));
  return file;
}
export async function inputFile(input) {
  if (input.localKey) {
    const file = await getLocal(input);
    if (!file) throw Error('请重新选择「' + input.name + '」，浏览器中没有这份本地文件。');
    return file;
  }
  const url = artifactURL(input.url);
  if (!url) throw Error('原始素材链接无效，请重新选择文件。');
  const response = await fetch(url);
  if (!response.ok) throw Error('无法读取原始素材「' + input.name + '」，请重新选择文件。');
  const blob = await response.blob();
  const ext = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/webp': '.webp',
    'model/gltf-binary': '.glb',
    'model/stl': '.stl'
  }[blob.type];
  const name =
    ext && !/\.(png|jpe?g|webp|obj|glb|stl)$/i.test(input.name) ? input.name + ext : input.name;
  return new File([blob], name, { type: blob.type });
}
