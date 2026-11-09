import { api, artifactURL } from './api.js';
import { recordedStructure, structureField } from './wear-structure.js';

const $ = (id) => document.getElementById(id);
const approvalStructure = structureField('approval-structure', '模型检查采用的佩戴结构', false);
const repairStructure = structureField('repair-structure', '模型检查采用的佩戴结构', false);
$('approval-structure-field').append(approvalStructure.group);
$('repair-structure-field').append(repairStructure.group);

export function checkStructure(p, action) {
  if (p?.source_type === 'text') return {};
  const chosen = action === 'approve' ? approvalStructure.select : repairStructure.select;
  return { structure: chosen.value };
}
export const labels = {
  queued: '排队中',
  generating: '云端生成中',
  preparing: '检查模型中',
  preview: '待确认外观',
  repairing: '修复模型中',
  ready: '模型已就绪',
  review: '需要复核',
  fitting: '适配中',
  slicing: '切片中',
  done: '已完成',
  failed: '需要处理',
  cancelled: '已取消'
};
export const busy = new Set(['queued', 'generating', 'preparing', 'repairing', 'fitting', 'slicing']);
export const qualityOf = (p) => p?.source_report?.quality;
export const cloudSource = (p) => ['images', 'text'].includes(p?.source_type);
export const pendingApproval = (p) => cloudSource(p) && p.preview_approved === false;
export const canPreview = (p) => Boolean(p?.assets?.glb || p?.assets?.model);
export const canFit = (p) =>
  Boolean(
    p?.source_ready && !pendingApproval(p) && ['passed', 'repaired'].includes(qualityOf(p)?.status)
  );
export function waitingForGeneration(p) {
  if (!cloudSource(p) || !['queued', 'generating', 'preparing'].includes(p.stage)) return false;
  return p.task_kind ? p.task_kind === 'source' : !p.source_ready;
}
export function titleFor(p) {
  if (p?.stage === 'done' && p.report?.blocking?.length) return '需要调整';
  if (waitingForGeneration(p) && p.stage === 'preparing') return '准备外观预览';
  return labels[p?.stage] || '待开始';
}
export function showProgress(project) {
  const p = project || {},
    quality = qualityOf(p);
  const states = {
    source: p.source_ready || p.stage === 'preview' ? 'done' : 'pending',
    approval: p.preview_approved === true ? 'done' : p.stage === 'preview' ? 'active' : 'pending',
    check: canFit(p) ? 'done' : quality?.status === 'needs_review' ? 'issue' : 'pending',
    fit: p.assets?.fitted ? 'done' : 'pending',
    slice: p.stage === 'done' && p.assets?.plates?.length ? 'done' : 'pending'
  };
  const working = {
    generating: 'source',
    preparing: waitingForGeneration(p) ? 'source' : 'check',
    repairing: 'check',
    fitting: 'fit',
    slicing: 'slice'
  };
  if (p.stage in working) states[working[p.stage]] = 'active';
  if (p.stage === 'failed') {
    const stage =
      p.task_kind === 'repair'
        ? 'check'
        : p.task_kind === 'source'
          ? 'source'
          : p.report?.blocking?.length
            ? 'fit'
            : p.source_ready
              ? p.assets?.fitted
                ? 'slice'
                : 'fit'
              : 'source';
    states[stage] = 'issue';
  }
  const stages = [
    ['source', p.source_type ? (cloudSource(p) ? 'Lux3D 生成外形' : '导入本地模型') : '外形生成或导入']
  ];
  if (cloudSource(p) && typeof p.preview_approved === 'boolean')
    stages.push(['approval', '确认生成外观']);
  stages.push(['check', '源模型检查'], ['fit', '佩戴适配'], ['slice', 'P1S 切片']);
  const list = $('progress-steps');
  list.replaceChildren();
  const words = { pending: '待处理', done: '已完成', active: '处理中', issue: '需复核' };
  stages.forEach(([key, label]) => {
    const li = document.createElement('li');
    li.dataset.state = states[key];
    li.textContent = label;
    li.setAttribute('aria-label', label + ' · ' + words[states[key]]);
    list.append(li);
  });
}
function appendMetricRow(table, label, before, after) {
  const tr = document.createElement('tr');
  for (const [tag, value] of [
    ['th', label],
    ['td', before ?? '—'],
    ['td', after ?? '—']
  ]) {
    const cell = document.createElement(tag);
    cell.textContent = String(value);
    tr.append(cell);
  }
  table.append(tr);
}
export function showQuality(p) {
  const quality = qualityOf(p);
  $('mesh-review').hidden = !p?.source_ready || pendingApproval(p);
  if ($('mesh-review').hidden) return;
  const status = quality?.status;
  $('mesh-review').dataset.state = status || 'legacy';
  const waitingForCheck = p.stage === 'queued' && p.task_kind === 'repair';
  const working = waitingForCheck || ['preparing', 'repairing'].includes(p.stage);
  $('quality-title').textContent = working
    ? waitingForCheck
      ? '等待网格检查'
      : titleFor(p)
    : { passed: '网格检查通过', repaired: '网格已修复', needs_review: '请复核制造网格' }[status] ||
      '旧版本模型待检查';
  $('quality-message').textContent = working
    ? p.message
    : quality?.message || '可先查看原有结果。再次制作前，请检查当前源网格。';
  if (!working && status === 'needs_review') $('quality-message').textContent += ' 当前保留原网格。';
  const container = $('quality-counts');
  container.replaceChildren();
  if (quality?.before || quality?.after) {
    const table = document.createElement('table');
    table.className = 'quality-table';
    const header = document.createElement('thead');
    appendMetricRow(header, '结构检查', '修复前', '处理后');
    table.append(header);
    const body = document.createElement('tbody');
    for (const [key, label] of [
      ['boundary_edges', '开边'],
      ['nonmanifold_edges', '非流形边'],
      ['intersections', '面相交对']
    ]) {
      appendMetricRow(body, label, quality.before?.[key], quality.after?.[key]);
    }
    if (quality.pits?.before_count > 0)
      appendMetricRow(
        body,
        '局部凹坑',
        quality.pits.before_count,
        '补形 ' + (quality.pits.patched_count || 0)
      );
    table.append(body);
    container.append(table);
  }
  $('repair-settings').open = !canFit(p);
  $('inspect-defect').hidden = !quality?.pits?.points_mm?.length;
  $('repair').disabled = busy.has(p.stage);
  $('compare-original').disabled = !p.assets?.original_model && !p.assets?.glb;
  $('compare-prepared').disabled = !p.assets?.model || status === 'needs_review';
}
const modeNames = { single: '单图生成', multi: '多视图生成', model: '导入本地模型', text: '文字生成' };
export function sourceLabel(p) {
  if (p?.source_type === 'text') return modeNames.text;
  return modeNames[p?.input_mode] || (p?.source_type === 'model' ? '导入本地模型' : '图片生成');
}
export function showGenerationInfo(p,submitting) {
  const confirm=pendingApproval(p) && p.stage==='preview' && Boolean(p.assets?.glb);
  const resolved=recordedStructure(p);
  $('candidate-actions').hidden=!confirm;
  $('approve-candidate').disabled=!confirm||submitting;
  $('candidate-check-hint').textContent=p.source_type==='text' && resolved==='cap_mask'
    ? '请转到后视查看实际开口，再检查双眼。确认将采用当前预览模型。'
    : '请检查正面、背面和轮廓。确认将采用当前预览模型。';
  $('approval-structure-field').hidden=p.source_type!=='images'||!confirm;
  $('repair-structure-field').hidden=$('repair-structure-hint').hidden=p.source_type==='text';
  approvalStructure.select.disabled=!confirm||submitting;
  repairStructure.select.disabled=submitting||busy.has(p.stage);
  approvalStructure.load(p.id,resolved);repairStructure.load(p.id,resolved);
}

export function setupHistory(currentId) {
  const list = $('library-list');
  const feedback = document.createElement('p');
  feedback.className = 'history-feedback';
  feedback.setAttribute('role', 'status');
  feedback.hidden = true;
  list.after(feedback);
  let timer = 0,
    token = 0,
    signature = '';
  let projects = [],
    confirmId = '',
    removingId = '',
    error = '';
  function announce(text) {
    feedback.textContent = text;
    feedback.hidden = !text;
  }
  function control(label, className, action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.className = className;
    button.addEventListener('click', action);
    return button;
  }
  function confirm(project) {
    if (removingId || busy.has(project.stage)) return;
    if (confirmId === project.id) { dismiss(project.id); return; }
    confirmId = project.id;
    error = '';
    announce('');
    render(projects);
    list.querySelector('.history-keep')?.focus({ preventScroll: true });
  }
  function dismiss(id) {
    if (removingId) return;
    confirmId = '';
    error = '';
    render(projects);
    list.querySelector(`[data-project="${CSS.escape(id)}"] .history-remove`)?.focus({ preventScroll: true });
  }
  async function remove(project) {
    if (removingId || confirmId !== project.id || busy.has(project.stage)) return;
    removingId = project.id;
    error = '';
    render(projects);
    try {
      const result = await api('/api/projects/' + encodeURIComponent(project.id), { method: 'DELETE' });
      if (result.deleted !== project.id) throw Error('未确认删除结果，请刷新作品历史。');
      // An overview request started before DELETE can still contain the old row.
      ++token;
      projects = projects.filter(p => p.id !== project.id);
      confirmId = '';
      window.dispatchEvent(new CustomEvent('helmetfit:project-deleted', { detail: { id: project.id } }));
      if (location.hash === '#p/' + encodeURIComponent(project.id)) location.hash = 'new';
      announce('已删除《' + (project.name || '未命名作品') + '》。');
    } catch (e) {
      error = e.message;
    } finally {
      removingId = '';
      const restoreFocus = document.activeElement === document.body || list.contains(document.activeElement);
      render(projects);
      if (restoreFocus) {
        const next = list.querySelector(error ? '.history-keep' : '.library-row') || $('refresh-history');
        next.focus({ preventScroll: true });
      }
      refresh();
    }
  }
  function confirmation(project) {
    const box = document.createElement('div');
    box.className = 'history-confirm';
    box.id = 'history-confirm-' + project.id;
    box.setAttribute('role', 'group');
    const question = document.createElement('p');
    question.id = box.id + '-label';
    question.textContent = '删除《' + (project.name || '未命名作品') + '》？';
    box.setAttribute('aria-labelledby', question.id);
    const info = document.createElement('p');
    info.textContent = '此作品的文件和所有已存版本也会删除。';
    const actions = document.createElement('div');
    const keep = control('取消', 'history-keep', () => dismiss(project.id));
    const destroy = control(removingId ? '正在删除…' : '确认删除', 'history-destroy', () => remove(project));
    keep.disabled = destroy.disabled = Boolean(removingId);
    actions.append(keep, destroy);
    box.append(question, info, actions);
    if (error) {
      const issue = document.createElement('p');
      issue.setAttribute('role', 'alert');
      issue.textContent = error;
      box.append(issue);
    }
    box.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); dismiss(project.id); }
    });
    return box;
  }
  function note(text) {
    const el = document.createElement('p');
    el.className = 'muted';
    el.textContent = text;
    list.replaceChildren(el);
  }
  function render(items) {
    projects = items;
    if (confirmId && !removingId && !items.some(p => p.id === confirmId && !busy.has(p.stage))) {
      if (error) announce(error);
      confirmId = '';
      error = '';
    }
    const id = currentId();
    const hash = JSON.stringify([
      id, confirmId, removingId, error,
      items.map((p) => [p.id, p.name, p.stage, p.updated, p.wear_structure, p.reference_views?.[0]?.url])
    ]);
    if (hash === signature) return;
    signature = hash;
    if (!items.length) {
      note('暂无作品。新建后会保存在这里。');
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const p of items) {
      const row = document.createElement('div');
      row.className = 'history-entry';
      row.dataset.project = p.id;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'library-row';
      btn.setAttribute('aria-current', String(p.id === id));
      const thumb = document.createElement('span');
      thumb.className = 'history-thumb';
      const src = artifactURL(p.reference_views?.[0]?.url);
      if (src) {
        const img = document.createElement('img');
        img.src = src;
        img.alt = '';
        img.loading = 'lazy';
        thumb.append(img);
      } else
        thumb.textContent = p.source_type === 'model' ? '3D' : p.source_type === 'text' ? 'TXT' : 'IMG';
      const text = document.createElement('span'),
        name = document.createElement('strong'),
        status = document.createElement('small');
      name.textContent = p.name || '未命名作品';
      const cut =
        p.source_type === 'text' && p.wear_structure
          ? p.wear_structure === 'cap_mask'
            ? ' · 开放后脑'
            : ' · 全包'
          : '';
      status.textContent = sourceLabel(p) + cut + ' · ' + titleFor(p);
      btn.title = p.name + ' · ' + status.textContent;
      text.append(name, status);
      btn.append(thumb, text);
      btn.addEventListener('click', () => {
        location.hash = 'p/' + encodeURIComponent(p.id);
      });
      const removeButton = control('删除', 'history-remove', () => confirm(p));
      removeButton.setAttribute('aria-label', '删除作品：' + name.textContent);
      removeButton.disabled = Boolean(removingId) || busy.has(p.stage);
      removeButton.title = busy.has(p.stage) ? '任务结束或取消后可删除' : '删除这件作品';
      removeButton.setAttribute('aria-expanded', String(confirmId === p.id));
      row.append(btn, removeButton);
      if (confirmId === p.id) {
        removeButton.setAttribute('aria-controls', 'history-confirm-' + p.id);
        row.append(confirmation(p));
      }
      fragment.append(row);
    }
    list.replaceChildren(fragment);
  }
  async function refresh() {
    clearTimeout(timer);
    const active = ++token;
    try {
      const result = await api('/api/projects?overview=1');
      if (active !== token) return;
      render(result.projects || []);
    } catch (e) {
      if (active === token && !list.querySelector('.library-row')) {
        signature = '';
        note(e.message);
      }
    } finally {
      if (active === token) timer = setTimeout(refresh, document.hidden ? 12000 : 4000);
    }
  }
  $('refresh-history').addEventListener('click', refresh);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
  $('projects-open').addEventListener('click', () => {
    $('project-library').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('project-library').focus({ preventScroll: true });
  });
  refresh();
  return refresh;
}
