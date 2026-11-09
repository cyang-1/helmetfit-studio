import { api, artifactURL, message, uuid } from './api.js';
import { makeSlot, views } from './upload-views.js';
import { createTextFields } from './upload-text.js';
import { structureField } from './wear-structure.js';
import {
  draftFor,
  saveDraft,
  discardDraft,
  requestId,
  localInput,
  retainLocal,
  releaseLocal,
  getLocal,
  inputFile
} from './upload-drafts.js';

const form = document.getElementById('source-form');
form.innerHTML = `
  <div class="upload-mode" role="group" aria-label="输入方式">
    <button type="button" data-source="single">单图</button><button type="button" data-source="multi">多视图</button>
    <button type="button" data-source="text">文字</button><button type="button" data-source="model">导入模型</button>
  </div>
  <p id="source-note" class="upload-note"></p><div id="upload-slots"></div><div class="upload-format" id="upload-format"></div>
  <label class="field">作品名称 <span>选填</span><input id="project-name" maxlength="64" autocomplete="off" placeholder="例如：巡航员头盔"></label>
  <p id="draft-files-status" class="upload-format" role="status" hidden></p><p id="upload-error" class="error-text" role="alert" hidden></p>
  <button id="create-submit" class="primary" type="submit" disabled>生成头盔外形</button>
  <div class="upload-examples"><button id="use-example" type="button">使用示例参考图</button><button id="open-local" type="button">打开头盔示例</button></div>`;
const $ = (id) => document.getElementById(id);
const slots = $('upload-slots'),
  errorBox = $('upload-error'),
  statusBox = $('draft-files-status');
const modeButtons = [...form.querySelectorAll('[data-source]')];
const textFields = createTextFields(() => {
  if (!draft) return;
  draft.text = textFields.values();
  changed();
});
const modelStructure = structureField('import-structure', '佩戴结构', false);
modelStructure.select.addEventListener('change', () => {
  if (!draft) return;
  draft.modelStructure = modelStructure.select.value;
  changed();
});

let draft = null,
  scope = '',
  renderVersion = 0,
  verifiedScope = false;
const pending = new Set();
const localURLs = new Map();
const missing = new Set();
const volatile = new Set();
const savingFiles = new Set();
const deleted = new Set();
const MiB = 1024 * 1024;
const unknownKey = (key) => key.startsWith('unassigned-');

function selected(d) {
  if (d.mode === 'model') return d.inputs.model ? [['model', d.inputs.model]] : [];
  if (d.mode === 'single') return d.inputs.reference ? [['reference', d.inputs.reference]] : [];
  if (d.mode === 'text') return [];
  const keys = [...views.map(([role]) => role), ...Object.keys(d.inputs).filter(unknownKey)];
  return keys.filter((key) => d.inputs[key]).map((key) => [key, d.inputs[key]]);
}
function isReady(d) {
  if (!d) return false;
  if (d.mode === 'text') return textFields.valid();
  const list = selected(d);
  if (!list.length || list.some(([, input]) => missing.has(input.localKey))) return false;
  if (d.mode !== 'multi') return true;
  if (list.length < 2 || list.length > 8) return false;
  if (list.every(([key]) => unknownKey(key))) return true;
  return Boolean(d.inputs.front) && list.every(([key]) => !unknownKey(key));
}
function draftFileStatus(d, files) {
  const absent = files.filter(([, f]) => missing.has(f.localKey));
  if (absent.length) return '请重新选择本地文件：' + absent.map(([, f]) => f.name).join('、');
  if (files.some(([, f]) => volatile.has(f.localKey)))
    return '本地文件仅在当前页面内可用，刷新后需重新选择。';
  if (files.some(([, f]) => savingFiles.has(f.localKey)))
    return '正在本机保存文件草稿，完成后可刷新继续。';
  if (files.some(([key]) => unknownKey(key)) && files.some(([key]) => !unknownKey(key)))
    return '请为旧参考图分配方向或移除它，再与新增视角一起生成。';
  if (d.mode === 'model' && d.missingModel && !d.inputs.model)
    return '原始导入文件不可读取，请重新选择本地模型。';
  if (files.some(([, f]) => f.localKey)) return '本地文件已缓存，可刷新后继续。';
  return '';
}
function updateControls() {
  const locked = !draft || pending.has(scope);
  for (const button of modeButtons) {
    button.disabled = locked;
    button.setAttribute('aria-pressed', String(draft?.mode === button.dataset.source));
  }
  for (const el of slots.querySelectorAll('input,select,button,textarea')) el.disabled = locked;
  for (const id of ['project-name', 'use-example', 'open-local']) $(id).disabled = locked;
  $('create-submit').disabled = locked || !verifiedScope || !isReady(draft);
  $('create-submit').textContent =
    locked && draft
      ? '正在提交…'
      : draft?.mode === 'model'
        ? '导入模型'
        : scope === 'new'
          ? '生成头盔外形'
          : '按当前输入生成';
  message(statusBox, draft ? draftFileStatus(draft, selected(draft)) : '');
}
function changed() {
  draft.ids[draft.mode] = uuid();
  saveDraft(draft);
  message(errorBox, '');
  updateControls();
}
function thumbnail(input) {
  return input?.url ? artifactURL(input.url) : localURLs.get(input?.localKey);
}
function inspect(file, url) {
  $('image-title').textContent = file.name;
  $('reference-full').src = url;
  $('reference-full').alt = file.name;
  $('image-dialog').showModal();
}
function render() {
  const version = ++renderVersion;
  slots.replaceChildren();
  $('project-name').value = draft?.names[draft.mode] || '';
  if (!draft) {
    $('source-note').textContent = '正在读取作品输入…';
    $('upload-format').hidden = true;
    updateControls();
    return;
  }
  const d = draft,
    mode = d.mode;
  slots.dataset.mode = mode;
  const card = (key) =>
    makeSlot(key, mode, d.inputs[key], thumbnail(d.inputs[key]), { assign, move, preview: inspect });
  if (mode === 'text') {
    textFields.restore(d.text);
    slots.append(textFields.panel);
  } else if (mode === 'model') {
    modelStructure.set(d.modelStructure);
    slots.append(card('model'), modelStructure.group);
  } else if (mode === 'single') {
    slots.append(card('reference'));
  } else {
    slots.append(card('front'));
    const grid = document.createElement('div');
    grid.className = 'angle-grid';
    for (const [key] of views.slice(1, 4)) grid.append(card(key));
    slots.append(grid);
    const extras = document.createElement('details');
    extras.className = 'extra-angles';
    extras.open = d.extraOpen;
    const summary = document.createElement('summary');
    summary.textContent = '更多视角';
    const extraGrid = document.createElement('div');
    extraGrid.className = 'angle-grid';
    for (const [key] of views.slice(4)) extraGrid.append(card(key));
    extras.append(summary, extraGrid);
    slots.append(extras);
    extras.addEventListener('toggle', () => {
      if (d === draft && extras.isConnected) {
        d.extraOpen = extras.open;
        saveDraft(d);
      }
    });
    const legacy = Object.keys(d.inputs).filter(unknownKey);
    if (legacy.length) {
      const hint = document.createElement('p');
      hint.className = 'upload-format';
      hint.textContent = '旧参考图将按原顺序提交，也可为它们分配视角。';
      const unassigned = document.createElement('div');
      unassigned.className = 'angle-grid';
      for (const key of legacy) unassigned.append(card(key));
      slots.append(hint, unassigned);
    }
  }
  $('source-note').textContent = {
    single: '上传一张草图、概念图或照片，由 Aholo Lux3D 生成外形。',
    multi: '正面和至少一个补充视角。左右以佩戴者为准。',
    text: '描述主题，再分别选择造型方向和佩戴结构。',
    model: '使用原始头盔文件，检查网格与尺寸。'
  }[mode];
  $('upload-format').hidden = mode === 'text';
  $('upload-format').textContent =
    mode === 'model'
      ? 'GLB、STL、OBJ · 最大 150 MB'
      : mode === 'multi'
        ? 'PNG、JPG、WebP · 每张 ≤ 16 MB，合计 ≤ 64 MB'
        : 'PNG、JPG、WebP · 最大 16 MB';
  $('use-example').textContent = mode === 'text' ? '使用「圣诞老人」示例' : '使用示例参考图';
  updateControls();
  for (const [role, input] of selected(d)) {
    if (!input.localKey || localURLs.has(input.localKey) || missing.has(input.localKey)) continue;
    getLocal(input).then((file) => {
      if (renderVersion !== version || d !== draft) return;
      if (!file) {
        missing.add(input.localKey);
        updateControls();
        return;
      }
      if (mode === 'model') return;
      const url = URL.createObjectURL(file);
      localURLs.set(input.localKey, url);
      const tile = slots.querySelector('[data-view="' + role + '"]');
      if (tile) tile.replaceWith(card(role));
      updateControls();
    });
  }
}
function freePreview(input) {
  if (!input?.localKey) return;
  const url = localURLs.get(input.localKey);
  if (url) URL.revokeObjectURL(url);
  localURLs.delete(input.localKey);
  volatile.delete(input.localKey);
  missing.delete(input.localKey);
}
function replaceInput(d, key, input) {
  const previous = d.inputs[key];
  if (previous) {
    freePreview(previous);
    releaseLocal(previous);
  }
  if (input) d.inputs[key] = input;
  else delete d.inputs[key];
}
function assign(role, files) {
  const d = draft;
  if (!d || pending.has(d.key)) return;
  if (files.length > 1) {
    message(errorBox, '每个位置只能放一个文件。');
    return;
  }
  const f = files[0],
    model = role === 'model';
  if (f) {
    if (
      !(model ? /\.(glb|stl|obj)$/i : /\.(png|jpe?g|webp)$/i).test(f.name) ||
      !f.size ||
      f.size > (model ? 150 : 16) * MiB
    ) {
      message(
        errorBox,
        model
          ? '请选择 150 MB 以内的 GLB、STL 或 OBJ 模型。'
          : '请选择 16 MB 以内的 PNG、JPG 或 WebP 图片。'
      );
      return;
    }
    const others = selected(d).filter(([key]) => key !== role);
    if (
      !model &&
      others.some(([, x]) => x.name === f.name && x.size === f.size && x.lastModified === f.lastModified)
    ) {
      message(errorBox, '这张图片已在另一个视角中。');
      return;
    }
    if (
      !model &&
      (others.length >= 8 || others.reduce((s, [, x]) => s + (x.size || 0), f.size) > 64 * MiB)
    ) {
      message(errorBox, '每个作品最多八张参考图，合计不能超过 64 MB。');
      return;
    }
    const input = localInput(f);
    replaceInput(d, role, input);
    savingFiles.add(input.localKey);
    retainLocal(input)
      .catch(() => volatile.add(input.localKey))
      .finally(() => {
        savingFiles.delete(input.localKey);
        if (d === draft) updateControls();
      });
    if (model) d.missingModel = false;
  } else replaceInput(d, role, null);
  changed();
  render();
}
function move(from, to) {
  if (
    !draft ||
    pending.has(scope) ||
    from === to ||
    !draft.inputs[from] ||
    !views.some(([key]) => key === to)
  )
    return;
  const prev = draft.inputs[to];
  draft.inputs[to] = draft.inputs[from];
  if (prev) draft.inputs[from] = prev;
  else delete draft.inputs[from];
  if (views.slice(4).some(([key]) => key === to)) draft.extraOpen = true;
  changed();
  render();
}
for (const button of modeButtons)
  button.addEventListener('click', () => {
    if (!draft || pending.has(scope) || draft.mode === button.dataset.source) return;
    draft.mode = button.dataset.source;
    saveDraft(draft);
    message(errorBox, '');
    render();
  });
$('project-name').addEventListener('input', () => {
  if (draft) {
    draft.names[draft.mode] = $('project-name').value;
    changed();
  }
});
$('use-example').addEventListener('click', () => {
  if (!draft || pending.has(scope)) return;
  if (draft.mode === 'text') {
    textFields.example();
    return;
  }
  if (draft.mode === 'model') draft.mode = 'single';
  const required = draft.mode === 'single' ? ['reference'] : ['front', 'left'];
  for (const [key] of selected(draft)) replaceInput(draft, key, null);
  for (const key of required) {
    const stem = key === 'reference' ? 'front' : 'angle-' + key;
    draft.inputs[key] = { name: stem + '.png', url: '/static/lux-example/' + stem + '.png' };
  }
  if (!draft.names[draft.mode]) draft.names[draft.mode] = '巡航员头盔';
  changed();
  render();
});
async function payload(d, example) {
  if (example) {
    const file = await inputFile({ name: 'crescent.glb', url: '/static/studio-example/helmet.glb' });
    const body = new FormData();
    body.set('model', file);
    body.set('name', '弦月勘探盔');
    body.set('input_mode', 'model');
    body.set('structure', 'full_shell');
    body.set('request_id', d.exampleId);
    return { body };
  }
  const id = requestId(d, d.mode),
    name = (d.names[d.mode] || '').trim();
  if (d.mode === 'text') {
    return {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        input_mode: 'text',
        ...d.text,
        theme: d.text.theme.trim(),
        name,
        request_id: id
      })
    };
  }
  const inputs = selected(d);
  const files = await Promise.all(inputs.map(([, input]) => inputFile(input)));
  const body = new FormData(),
    model = d.mode === 'model';
  if (
    !model &&
    (files.some((f) => f.size > 16 * MiB) || files.reduce((sum, f) => sum + f.size, 0) > 64 * MiB)
  ) {
    throw Error('每张参考图限 16 MB，合计限 64 MB。');
  }
  for (const f of files) body.append(model ? 'model' : 'images', f);
  body.set('name', name || files[0].name.replace(/\.[^.]+$/, ''));
  body.set('request_id', id);
  // Legacy multi-view assets have a stable order but no recorded camera angles.
  // Omitting both fields retains that order under the original upload contract.
  if (!inputs.every(([key]) => unknownKey(key))) {
    body.set('input_mode', d.mode);
    if (model) body.set('structure', d.modelStructure);
    else body.set('view_labels', JSON.stringify(inputs.map(([key]) => key)));
  }
  return { body };
}
async function submit(example = false) {
  const d = draft,
    route = location.hash;
  const routeKey = route.match(/^#p\/([a-zA-Z0-9_-]+)$/)?.[1] || 'new';
  if (!d || !verifiedScope || d.key !== routeKey || pending.has(d.key) || (!example && !isReady(d)))
    return;
  pending.add(d.key);
  updateControls();
  message(errorBox, '');
  saveDraft(d);
  try {
    const options = await payload(d, example);
    if (deleted.has(d.key)) return;
    const created = await api('/api/projects', { method: 'POST', ...options });
    if (!created.id) throw Error('未取得作品编号，请在历史中查询。');
    if (!deleted.has(d.key)) {
      if (example) d.exampleId = uuid();
      else d.ids[d.mode] = uuid();
      saveDraft(d);
    }
    if (deleted.has(created.id)) return;
    const next = draftFor(created.id, created);
    if (!example && d.mode === 'text') next.text = { ...d.text };
    saveDraft(next);
    if (!example && d.key === 'new' && d === draft && location.hash === route) discardDraft('new');
    if (d === draft && location.hash === route) location.hash = 'p/' + encodeURIComponent(created.id);
  } catch (e) {
    if (d === draft && !deleted.has(d.key)) message(errorBox, e.message);
  } finally {
    pending.delete(d.key);
    if (d === draft) updateControls();
  }
}
form.addEventListener('submit', (e) => {
  e.preventDefault();
  submit();
});
$('create-submit').addEventListener('click', (e) => {
  if (e.detail > 1) e.preventDefault();
});
$('open-local').addEventListener('click', (e) => {
  if (e.detail <= 1) submit(true);
});

// Active inputs belong to the selected history record. Polling that record
// can update its job status without replacing typed text or selected files.
export function bindComposer(id = 'new', project = null) {
  const next = draftFor(id, project);
  if (scope === id && draft === next) {
    if (project && !verifiedScope) {
      verifiedScope = true;
      updateControls();
    }
    return;
  }
  verifiedScope = id === 'new' || Boolean(project);
  for (const url of localURLs.values()) URL.revokeObjectURL(url);
  localURLs.clear();
  missing.clear();
  scope = id;
  draft = next;
  message(errorBox, '');
  render();
}
addEventListener('helmetfit:project-deleted', (e) => {
  const key = e.detail?.id;
  if (!key) return;
  deleted.add(key);
  discardDraft(key);
  if (scope === key) {
    draft = null;
    render();
  }
});
