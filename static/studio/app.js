import { bindComposer } from './upload.js';
import {
  busy,
  canPreview,
  checkStructure,
  canFit,
  cloudSource,
  pendingApproval,
  qualityOf,
  setupHistory,
  showGenerationInfo,
  showProgress,
  showQuality,
  titleFor,
  waitingForGeneration
} from './workspace.js';
import { renderOutputFiles } from './outputs.js';
import { recordedStructure } from './wear-structure.js';
import { api, sendJSON, message, artifactURL } from './api.js';
import {
  field,
  headKeys,
  sizeKeys,
  setFitStructure,
  poseKeys,
  populate,
  values,
  refreshEyeFields,
  validateFit
} from './fit-form.js';

const $ = (id) => document.getElementById(id);
let project = null,
  routeToken = 0,
  pollToken = 0,
  poll = null,
  viewer = null,
  viewerLoading = null;
let drafted = false,
  editing = false,
  initializedSource = false,
  commandsPending = false;
let cutterTimer = null,
  cutterToken = 0,
  cutterKey = '',
  cutterAssets = {},
  visualToken = 0;
let selectedRevision = 'current',
  baseline = {},
  draftTimer = null,
  stageMemory = '';
let step = 'source',
  reviewSignature = '';
let lastSuggestion = null,
  scaleAdjustment = null,
  updateCutterNow = null;
let poseTool = 'orbit', centering = false;
const poseButtons = [...document.querySelectorAll('[data-pose-tool]')];
const poseHints = {
  orbit: '拖拽旋转视角 · 滚轮缩放',
  translate: '拖动箭头移动头盔 · 拖动空白处旋转视角',
  rotate: '拖动轴环旋转头盔 · 拖动空白处旋转视角',
  scale: '拖动任一轴端方块等比缩放 · 拖动空白处旋转视角'
};
function canManipulate() {
  return step === 'fit' && selectedRevision === 'current' && canFit(project)
    && !commandsPending && !busy.has(project.stage);
}
function updatePoseToolbar() {
  const enabled = canManipulate();
  $('pose-tools').hidden = !enabled;
  if (!enabled || $('preview-mode').value !== 'model') {
    poseTool = 'orbit';
    viewer?.releasePoseTool();
  }
  for (const button of poseButtons) button.setAttribute('aria-pressed', String(button.dataset.poseTool === poseTool));
  $('pose-center').disabled = centering;
  document.querySelector('.navigation-hint').textContent = poseHints[poseTool];
}
const approvalIntent = new Set();
function recordApproval(id, enabled) {
  if (enabled) approvalIntent.add(id);
  else approvalIntent.delete(id);
  try {
    const key = 'helmetfit:await-fit:' + id;
    if (enabled) sessionStorage.setItem(key, '1');
    else sessionStorage.removeItem(key);
  } catch {}
}
function awaitingFit(id) {
  if (approvalIntent.has(id)) return true;
  try {
    return sessionStorage.getItem('helmetfit:await-fit:' + id) === '1';
  } catch {
    return false;
  }
}

function getParamSignature(params) {
  return JSON.stringify(
    Object.keys(params)
      .sort()
      .map((key) => [key, params[key]])
  );
}
function draftKey(id) {
  return 'helmetfit:draft:' + id;
}
function readDraft(id) {
  try {
    return JSON.parse(localStorage.getItem(draftKey(id)) || 'null');
  } catch {
    return null;
  }
}
function saveDraft() {
  if (!project?.source_ready || !initializedSource) return;
  try {
    if (drafted)
      localStorage.setItem(
        draftKey(project.id),
        JSON.stringify({
          revision: project.revision,
          structure: recordedStructure(project),
          params: values(),
          scaleAdjustment
        })
      );
    else localStorage.removeItem(draftKey(project.id));
  } catch {
    /* A private browser session can still complete the current project. */
  }
}
function allRevisions() {
  return [project, ...(project?.history || []).filter(Boolean).slice().reverse()];
}
function selectedOutput() {
  if (selectedRevision === 'current') return project;
  return (
    allRevisions()
      .slice(1)
      .find((p) => 'revision-' + p.revision === selectedRevision) || project
  );
}
function savedStep(id) {
  try {
    return sessionStorage.getItem('helmetfit:step:' + id);
  } catch {
    return null;
  }
}
function displayStep(next, remember = false) {
  const allowFit =
    canFit(project) ||
    (!pendingApproval(project) && project?.stage === 'done' && project.assets?.fitted);
  step = next === 'fit' && allowFit ? 'fit' : 'source';
  $('step-source').setAttribute('aria-pressed', String(step === 'source'));
  $('step-fit').setAttribute('aria-pressed', String(step === 'fit'));
  $('step-fit').disabled = !allowFit;
  $('step-fit').title = allowFit
    ? '调整头型与打印设置'
    : pendingApproval(project)
      ? '请先确认生成外观'
      : '先生成并检查模型';
  $('create').hidden = step !== 'source';
  $('source-form').hidden = false;
  $('source-review').hidden = !project;
  $('editor').hidden = step !== 'fit';
  $('quality-gate').hidden = !project || canFit(project);
  $('continue-fit').hidden = !canFit(project);
  const waiting = waitingForGeneration(project);
  $('viewport-empty').hidden = canPreview(project) && !waiting;
  $('viewport-empty').classList.toggle('is-generating', waiting);
  $('viewport').setAttribute('aria-busy', String(waiting));
  $('empty-example').hidden = Boolean(project);
  $('view-tools').hidden = $('preview-mode').hidden = !canPreview(project) || waiting;
  $('view-controls').hidden = !project?.source_ready || step !== 'fit';
  updatePoseToolbar();
  if (remember && project) {
    try {
      sessionStorage.setItem('helmetfit:step:' + project.id, step);
    } catch {}
  }
}
function changeStep(next) {
  const previous = step;
  displayStep(next, true);
  if (!project || previous === step) return;
  $('preview-mode').value = step === 'fit' && !drafted && viewAsset('fitted') ? 'fitted' : 'model';
  selectedRevision = 'current';
  $('view-info').textContent = '';
  updateModeOptions();
  renderMesh();
  scheduleCutter();
  renderOutputs();
  controls();
  document.querySelector('.rail-content').scrollTop = 0;
}
function suggestions(p) {
  return (
    p.defaults ||
    p.source_report?.defaults ||
    p.prepare?.defaults ||
    p.prepare_report?.defaults ||
    p.report?.defaults ||
    {}
  );
}
function viewAsset(name) {
  const current = selectedOutput()?.assets || {};
  if (name === 'original') return artifactURL(project?.assets?.original_model);
  return artifactURL(name === 'model' || name === 'glb' ? project?.assets?.[name] : current[name]);
}
function settings() {
  return {
    mode: $('preview-mode').value,
    compareAligned: step === 'source',
    head: step === 'fit' && $('show-head').checked,
    cutter: step === 'fit' && $('show-cutter').checked,
    section: step === 'fit' && $('section').checked,
    depth: Number($('section-depth').value),
    poseTool: canManipulate() ? poseTool : 'orbit'
  };
}
async function getViewer() {
  if (viewer || viewerLoading) return viewer || viewerLoading;
  viewerLoading = import('./viewer.js')
    .then((module) => {
      viewer = module.createViewer($('viewport'), (next, commit) => {
        if (!canManipulate()) return;
        for (const k of poseKeys) {
          if (next[k] !== undefined) field(k).value = next[k];
        }
        if (commit) {
          scaleAdjustment = null;
          editFit();
        }
      });
      return viewer;
    })
    .catch(() => {
      message($('view-notice'), '三维预览暂不可用。请打开浏览器硬件加速，或下载 GLB 查看。');
      return null;
    });
  return viewerLoading;
}
async function renderMesh() {
  if (!canPreview(project) || waitingForGeneration(project)) return;
  const token = ++visualToken,
    mode = $('preview-mode').value;
  const engine = await getViewer();
  if (!engine || token !== visualToken) return;
  const output = selectedOutput(),
    overlay = mode === 'model' ? cutterAssets : output.assets || {};
  engine.sourceSize(project.source_report?.dimensions);
  engine.settings(settings());
  const url = viewAsset(mode);
  if (!url) {
    message($('view-notice'), '此版本尚无可查看的模型。');
    return;
  }
  const work = [];
  for (const name of ['model', 'original', 'glb', 'fitted', 'ring'])
    work.push(engine.load(name, name === mode ? url : null));
  for (const name of ['head', 'cutter']) {
    work.push(
      engine.load(
        name,
        (mode === 'model' || mode === 'fitted') && settings()[name] ? artifactURL(overlay[name]) : null
      )
    );
  }
  if (!engine.loadedURL || engine.loadedURL !== url) {
    message($('view-notice'), '正在读取网格…');
  }
  try {
    await Promise.all(work);
    if (token !== visualToken) return;
    if (mode === 'model') engine.applyPose(step === 'fit' ? values() : {});
    engine.loadedURL = url;
    message($('view-notice'), '');
  } catch (e) {
    if (token === visualToken)
      message(
        $('view-notice'),
        mode === 'glb' ? '原始材质预览无法加载。可下载 GLB 查看生成外观。' : e.message
      );
  }
}
function updateModeOptions() {
  $('compare-prepared').setAttribute('aria-pressed', String($('preview-mode').value === 'model'));
  $('compare-original').setAttribute(
    'aria-pressed',
    String(['original', 'glb'].includes($('preview-mode').value))
  );
  const select = $('preview-mode');
  select.querySelector('[value="ring"]').textContent =
    selectedOutput()?.report?.ring_kind === 'open_band' ? '开放试戴带' : '试戴环';
  for (const opt of select.options)
    opt.disabled = !viewAsset(opt.value) || (pendingApproval(project) && opt.value !== 'glb');
  if (select.selectedOptions[0]?.disabled)
    select.value = [...select.options].find((opt) => !opt.disabled)?.value || 'model';
  $('show-cutter').disabled = $('show-head').disabled = ['ring', 'glb'].includes(select.value);
  updatePoseToolbar();
}
function clearCutter() {
  clearTimeout(cutterTimer);
  cutterToken++;
  cutterKey = '';
  cutterAssets = {};
  lastSuggestion = null;
  scaleAdjustment = null;
  updateCutterNow = null;
}
function scheduleCutter() {
  if (step !== 'fit' || !canFit(project) || $('preview-mode').value !== 'model') return;
  const params = values(),
    id = project.id;
  if (headKeys.some((name) => !field(name).validity.valid)) return;
  const key = JSON.stringify([id, ...headKeys.map((name) => params[name])]);
  if (key === cutterKey && (!scaleAdjustment || updateCutterNow)) return;
  const token = ++cutterToken;
  cutterKey = key;
  clearTimeout(cutterTimer);
  cutterAssets = {};
  renderMesh();
  if (!$('show-cutter').checked && !$('show-head').checked && !scaleAdjustment) {
    cutterKey = '';
    return;
  }
  $('view-info').textContent = '正在更新头模和切除范围…';
  let active = null;
  const update = async () => {
    try {
      const data = await sendJSON('/api/projects/' + id + '/cutter', params);
      if (token !== cutterToken || project?.id !== id) return;
      const next = data.suggested_pose;
      if (scaleAdjustment) {
        if (!next?.scale) throw Error('无法按头型计算外壳尺寸，请重新启动到最新版本后重试。');
        if (scaleAdjustment.reset) {
          populate(Object.fromEntries(poseKeys.map((k) => [k, next[k]])));
        } else {
          const previous = scaleAdjustment.previous || data.saved_suggested_pose?.scale;
          const ratio = previous ? scaleAdjustment.scale / previous : 1;
          field('scale').value = Number(Math.min(6, Math.max(0.1, next.scale * ratio)).toFixed(4));
        }
        drafted = getParamSignature(values()) !== getParamSignature(baseline);
        scaleAdjustment = null;
        viewer?.applyPose(values());
        controls();
        saveDraft();
      }
      lastSuggestion = next ?? null;
      cutterAssets = data;
      await renderMesh();
      if (token === cutterToken)
        $('view-info').textContent =
          '蓝色代表头模，橙色代表实际切除范围。外壳缩放 ' + values().scale + ' 倍。';
      return true;
    } catch (e) {
      if (token === cutterToken) {
        cutterKey = '';
        $('view-info').textContent = e.message;
      }
      return false;
    } finally {
      if (token === cutterToken) updateCutterNow = null;
    }
  };
  updateCutterNow = () => (active ||= update());
  cutterTimer = setTimeout(updateCutterNow, 450);
}
function renderOutputs() {
  if (project)
    renderOutputFiles(project, selectedOutput(), allRevisions(), drafted, busy.has(project.stage));
}
function controls() {
  updatePoseToolbar();
  if (!project) return;
  const active = busy.has(project.stage);
  $('fit-submit').disabled = commandsPending || active || !canFit(project);
  $('repair').disabled = commandsPending || active || !project.source_ready || pendingApproval(project);
  $('continue-fit').disabled = commandsPending || active;
  $('retry').hidden = !['failed', 'cancelled'].includes(project.stage);
  $('retry').textContent = project.report?.blocking?.length
    ? '调整参数'
    : cloudSource(project)
      ? '重试当前任务'
      : '重试';
  $('cloud-recover').hidden = !project.uncertain || $('retry').hidden;
  $('cancel').hidden = !active;
  $('cancel').disabled = commandsPending;
  $('retry').disabled = commandsPending;
  showGenerationInfo(project, commandsPending);
  $('fit-detail').textContent = drafted
    ? '尺寸已修改。提交后将生成新版本。'
    : active
      ? '任务进行中，可从作品历史继续查看。'
      : !canFit(project)
        ? '当前模型须完成源网格检查才能制作新版本。'
        : '';
}
function initFit(p) {
  clearCutter();
  setFitStructure(recordedStructure(p));
  populate({ ...suggestions(p), ...p.params }, true);
  baseline = values();
  const draft = readDraft(p.id);
  if (
    draft &&
    String(draft.revision) === String(p.revision) &&
    (draft.structure || 'full_shell') === recordedStructure(p)
  ) {
    populate(draft.params);
    scaleAdjustment = draft.scaleAdjustment || null;
  }
  drafted = getParamSignature(values()) !== getParamSignature(baseline);
  $('preview-mode').value = p.assets?.fitted && !drafted ? 'fitted' : 'model';
  initializedSource = true;
}
function renderProject(p) {
  const old = project;
  const sourceAppeared = p.source_ready && !initializedSource;
  const revisionChanged = old && old.revision !== p.revision;
  const newSource = old?.assets?.model && old.assets.model !== p.assets?.model;
  const newStructure = old && recordedStructure(old) !== recordedStructure(p);
  project = p;
  if (sourceAppeared || newSource || newStructure || (revisionChanged && !editing)) initFit(p);
  if (!old)
    step =
      savedStep(p.id) ||
      (p.stage === 'done' ||
      ['fitting', 'slicing'].includes(p.stage) ||
      (p.stage === 'ready' && cloudSource(p) && p.preview_approved === true && canFit(p))
        ? 'fit'
        : 'source');
  if (newSource) {
    step = 'source';
    selectedRevision = 'current';
    $('preview-mode').value = 'model';
  }
  if ((!old || sourceAppeared || newSource) && step === 'source')
    $('preview-mode').value = p.stage === 'review' && p.assets?.original_model ? 'original' : 'model';
  if (pendingApproval(p)) {
    step = 'source';
    if (p.stage === 'preview') $('preview-mode').value = 'glb';
  }
  if (canFit(p) && p.stage === 'ready' && awaitingFit(p.id)) {
    step = 'fit';
    $('preview-mode').value = 'model';
    recordApproval(p.id, false);
    try {
      sessionStorage.setItem('helmetfit:step:' + p.id, 'fit');
    } catch {}
  }
  if (p.stage === 'review' && awaitingFit(p.id)) {
    step = 'source';
    recordApproval(p.id, false);
  }
  displayStep(step);
  $('project-title').textContent = p.name || '未命名作品';
  bindComposer(p.id,p);
  document.title = $('project-title').textContent + ' · 量身造盔';
  $('source-intro').textContent='可继续编辑当前输入，每次生成都会保存为独立作品。';
  $('project-stage').textContent = titleFor(p);
  $('project-stage').classList.toggle('warn', ['review', 'failed'].includes(p.stage));
  $('job-heading').textContent = p.name || '当前作品';
  $('job-message').textContent = p.message || titleFor(p);
  $('job-status').classList.toggle('error', p.stage === 'failed' || p.stage === 'review');
  $('empty-title').textContent = titleFor(p);
  $('empty-copy').textContent = p.message || '完成外形处理后将在此显示模型。';
  showProgress(p);
  const qualityKey = JSON.stringify([p.id, p.stage, qualityOf(p), p.source_ready, p.preview_approved]);
  if (qualityKey !== reviewSignature) {
    showQuality(p);
    reviewSignature = qualityKey;
  }
  controls();
  renderOutputs();
  if (!canPreview(p)) return;
  if (old?.stage !== 'done' && p.stage === 'done' && step === 'fit' && !drafted && p.assets?.fitted)
    $('preview-mode').value = 'fitted';
  updateModeOptions();
  const sig = JSON.stringify([
    p.id,
    p.stage,
    p.revision,
    p.assets?.glb,
    p.assets?.model,
    p.assets?.fitted,
    p.assets?.ring
  ]);
  if (stageMemory !== sig) {
    stageMemory = sig;
    renderMesh();
    scheduleCutter();
    refreshHistory();
  }
}
async function pollProject(id, token) {
  const currentPoll = ++pollToken;
  clearTimeout(poll);
  try {
    const data = await api('/api/projects/' + encodeURIComponent(id));
    if (token !== routeToken || currentPoll !== pollToken) return;
    renderProject(data);
    if (busy.has(data.stage) || data.stage === 'preview') {
      poll = setTimeout(() => pollProject(id, token), data.stage === 'preview' ? 5000 : 1800);
    }
  } catch (e) {
    if (token !== routeToken || currentPoll !== pollToken) return;
    if(e.status===404){
      dispatchEvent(new CustomEvent('helmetfit:project-deleted',{detail:{id}}));
      location.hash='new';
      return;
    }
    $('job-heading').textContent = '项目读取失败';
    $('job-message').textContent = e.message;
    $('job-status').classList.add('error');
    $('retry').hidden = false;
    poll = setTimeout(() => pollProject(id, token), 5000);
  }
}
function openRoute() {
  const token = ++routeToken;
  clearTimeout(poll);
  clearTimeout(draftTimer);
  saveDraft();
  clearCutter();
  visualToken++;
  viewer?.clear();
  project = null;
  initializedSource = false;
  stageMemory = '';
  selectedRevision = 'current';
  drafted = false;
  editing = false;
  commandsPending = false;
  $('cloud-recover').hidden = true;
  $('remote-id').value = '';
  const id = location.hash.match(/^#p\/([a-zA-Z0-9_-]+)$/)?.[1];
  bindComposer(id||'new');
  displayStep('source');
  $('outputs').hidden = true;
  $('output-checks').open = false;
  $('mesh-review').hidden = true;
  $('candidate-actions').hidden = true;
  $('source-intro').textContent = '用参考图、文字主题或本地模型开始。';
  message($('candidate-error'), '');
  $('project-title').textContent = '未载入模型';
  $('project-stage').textContent = '待开始';
  $('project-stage').classList.remove('warn');
  $('job-heading').textContent = '';
  $('job-message').textContent = '创建或选择作品后查看进度。';
  $('view-info').textContent = '';
  message($('view-notice'), '');
  message($('repair-error'), '');
  $('empty-title').textContent = '预览工作区';
  $('empty-copy').textContent = '生成或导入头盔后，在这里检查外形与佩戴空间。';
  reviewSignature = '';
  showProgress(null);
  refreshHistory();
  $('job-status').classList.remove('error');
  $('retry').hidden = true;
  $('cancel').hidden = true;
  message($('fit-error'), '');
  document.title = '量身造盔 · HelmetFit Studio';
  if (id) {
    $('project-title').textContent = '载入作品';
    $('project-stage').textContent = '连接中';
    $('job-heading').textContent = '正在读取作品';
    $('job-message').textContent = '';
    pollProject(id, token);
  }
  scrollTo({ top: 0 });
}
function editFit(event) {
  if (!project) return;
  if (sizeKeys.includes(event?.target?.name) && !scaleAdjustment) {
    scaleAdjustment = { scale: values().scale, previous: lastSuggestion?.scale };
  } else if (event?.target?.name === 'scale') {
    // An explicit scale edit takes precedence over a pending automatic resize.
    scaleAdjustment = null;
  }
  editing = true;
  refreshEyeFields();
  drafted = getParamSignature(values()) !== getParamSignature(baseline);
  selectedRevision = 'current';
  $('preview-mode').value = 'model';
  updateModeOptions();
  viewer?.applyPose(values());
  renderMesh();
  scheduleCutter();
  renderOutputs();
  controls();
  message($('fit-error'), '');
  clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraft, 300);
}
$('fit-form').noValidate = true;
$('fit-form').addEventListener('input', editFit);
$('reset-pose').addEventListener('click', () => {
  if (!project) return;
  scaleAdjustment = { reset: true };
  cutterKey = '';
  editFit();
});
$('reset-pose').textContent = '按当前头型自动匹配';
$('fit-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!canFit(project) || commandsPending || busy.has(project.stage) || !validateFit()) return;
  const id = project.id,
    token = routeToken;
  commandsPending = true;
  controls();
  message($('fit-error'), '');
  try {
    if (scaleAdjustment) {
      scheduleCutter();
      clearTimeout(cutterTimer);
      const applied = await updateCutterNow?.();
      if (token !== routeToken) return;
      if (!applied || scaleAdjustment) throw Error('自动匹配尚未完成，请检查尺寸或手动设定外形缩放。');
    }
    const submitted = { ...values(), rebuild_outer: false };
    const data = await sendJSON('/api/projects/' + id + '/fit', submitted);
    if (token !== routeToken) return;
    editing = false;
    drafted = false;
    baseline = values();
    saveDraft();
    selectedRevision = 'current';
    if (data.id) renderProject(data);
    await pollProject(id, token);
  } catch (err) {
    if (token === routeToken) message($('fit-error'), err.message);
  } finally {
    if (token === routeToken) {
      commandsPending = false;
      controls();
    }
  }
});
for (const action of ['retry', 'cancel'])
  $(action).addEventListener('click', async () => {
    const id = project?.id,
      token = routeToken;
    if (!id) {
      openRoute();
      return;
    }
    if (commandsPending) return;
    if (action === 'retry' && project.report?.blocking?.length) {
      changeStep('fit');
      $('pose-details').open = true;
      field('scale').focus();
      return;
    }
    commandsPending = true;
    controls();
    try {
      const body = {};
      if (action === 'retry' && project.uncertain) {
        const remote = $('remote-id').value.trim();
        if (remote) {
          if (!/^\d+$/.test(remote)) throw Error('请输入 Aholo 返回的数字任务号。');
          body.remote_id = remote;
        } else throw Error('请填入已核实的 Aholo 任务号。如果从未创建任务，请点“新建作品”。');
      }
      await sendJSON('/api/projects/' + id + '/' + action, body);
      if (token === routeToken) await pollProject(id, token);
    } catch (err) {
      if (token === routeToken) $('job-message').textContent = err.message;
    } finally {
      if (token === routeToken) {
        commandsPending = false;
        controls();
      }
    }
  });
$('preview-mode').addEventListener('change', () => {
  updateModeOptions();
  renderMesh();
  scheduleCutter();
});
$('output-revision').addEventListener('change', (e) => {
  selectedRevision = e.target.value;
  const output = selectedOutput();
  $('preview-mode').value = output.assets?.fitted ? 'fitted' : output.assets?.ring ? 'ring' : 'model';
  updateModeOptions();
  renderOutputs();
  renderMesh();
});
$('preview-version').addEventListener('click', () => {
  const output = selectedOutput();
  if (!output?.assets?.fitted) return;
  selectMode('fitted');
  $('view-info').textContent = '当前显示历史版本 ' + output.revision + ' 的佩戴成品。';
});
$('restore-version').addEventListener('click', () => {
  const output = selectedOutput();
  if (output === project || busy.has(project.stage) || !canFit(project)) return;
  clearCutter();
  populate(output.params, true);
  editFit();
  changeStep('fit');
  field('circumference').focus();
});
for (const id of ['show-head', 'show-cutter', 'section', 'section-depth'])
  $(id).addEventListener('input', () => {
    $('section-depth').hidden = !$('section').checked;
    viewer?.settings(settings());
    if (id === 'show-head' || id === 'show-cutter') {
      renderMesh();
      scheduleCutter();
    }
  });
$('view-front').addEventListener('click', () => viewer?.frame(true));
for (const button of poseButtons) {
  button.addEventListener('click', () => {
    if (!canManipulate()) return;
    poseTool = button.dataset.poseTool;
    if (poseTool !== 'orbit') {
      if ($('preview-mode').value !== 'model' || viewer?.loadedURL !== viewAsset('model'))
        message($('view-notice'), '正在读取可编辑的制造网格…');
      selectMode('model');
    }
    else viewer?.settings(settings());
    updatePoseToolbar();
  });
}
$('pose-center').addEventListener('click', async () => {
  if (!canManipulate() || centering) return;
  centering = true;
  const token = routeToken, pid = project.id;
  try {
    selectMode('model');
    await renderMesh();
    if (token !== routeToken || !canManipulate()) return;
    const params = values(), signature = getParamSignature(params);
    const head = cutterAssets.head || (await sendJSON('/api/projects/' + pid + '/cutter', params)).head;
    const offsets = await viewer.centerToHead(artifactURL(head));
    if (token !== routeToken || !canManipulate() || signature !== getParamSignature(values())) return;
    for (const [k,v] of Object.entries(offsets)) field(k).value = v;
    editFit();
    $('view-info').textContent = '头盔与头模的三轴中心已对齐。';
  } catch (e) {
    if (token === routeToken) message($('fit-error'), e.message);
  } finally {
    centering = false;
    updatePoseToolbar();
  }
});
$('view-home').addEventListener('click', () => viewer?.frame());
$('step-source').addEventListener('click', () => changeStep('source'));
$('step-fit').addEventListener('click', () => changeStep('fit'));
$('continue-fit').addEventListener('click', () => changeStep('fit'));
$('quality-gate').addEventListener('click', () => {
  changeStep('source');
  $('repair-settings').open = true;
  $('repair').focus();
});
$('new-project').addEventListener('click', () => {
  if (location.hash === '#new') {
    displayStep('source');
    $('source-form').querySelector('button,input')?.focus();
  } else location.hash = 'new';
});
$('empty-example').addEventListener('click', () => $('open-local')?.click());
$('close-image').addEventListener('click', () => $('image-dialog').close());
function selectMode(mode) {
  $('preview-mode').value = mode;
  updateModeOptions();
  renderMesh();
  scheduleCutter();
}
$('compare-original').addEventListener('click', () =>
  selectMode(viewAsset('original') ? 'original' : 'glb')
);
$('compare-prepared').addEventListener('click', () => selectMode('model'));
$('view-back').addEventListener('click', () => viewer?.frame('rear'));
$('inspect-defect').addEventListener('click', async () => {
  const originalMode = viewAsset('original') ? 'original' : 'model';
  if (!['original', 'model'].includes($('preview-mode').value)) $('preview-mode').value = originalMode;
  updateModeOptions();
  await renderMesh();
  const pts = qualityOf(project)?.pits?.points_mm || [];
  const focus = viewer?.focusInspection(pts);
  if (focus)
    $('view-info').textContent = '检查区域 ' + focus + ' / ' + pts.length + '。再次点击定位下一处。';
});
$('repair-method').addEventListener('change', () => {
  $('repair-hint').textContent =
    $('repair-method').value === 'outer'
      ? '按可见外表面重建制造网格。深凹槽和已有开口可能被填平，请检查修复后的外形。'
      : '检查开边、非流形边及面自交，并尝试保留原有轮廓。';
});
$('repair-method').dispatchEvent(new Event('change'));
$('repair').addEventListener('click', async () => {
  if (!project?.source_ready || pendingApproval(project) || commandsPending || busy.has(project.stage))
    return;
  const id = project.id,
    token = routeToken;
  commandsPending = true;
  controls();
  message($('repair-error'), '');
  try {
    const result = await sendJSON('/api/projects/' + id + '/repair', {
      method: $('repair-method').value,
      ...checkStructure(project, 'repair')
    });
    if (token !== routeToken) return;
    if (result.id) renderProject(result);
    await pollProject(id, token);
  } catch (e) {
    if (token === routeToken) message($('repair-error'), e.message);
  } finally {
    if (token === routeToken) {
      commandsPending = false;
      controls();
    }
  }
});
$('approve-candidate').addEventListener('click', async () => {
  if (!pendingApproval(project) || commandsPending || project.stage !== 'preview' || !project.assets?.glb) return;
  const {id}=project,token=routeToken;
  commandsPending=true;
  clearTimeout(poll);pollToken++;controls();
  message($('candidate-error'),'');
  try {
    recordApproval(id,true);
    const next=await sendJSON('/api/projects/'+id+'/approve',checkStructure(project,'approve'));
    if(token!==routeToken)return;
    if(next.id)renderProject(next);
    await pollProject(id,token);
  } catch(error) {
    if(token!==routeToken)return;
    message($('candidate-error'),error.message);
    await pollProject(id,token);
    if(project?.stage==='preview')recordApproval(id,false);
  } finally {
    if(token===routeToken){commandsPending=false;controls();}
  }
});
addEventListener('helmetfit:project-deleted',event=>{
  const id=event.detail?.id;
  if(!id)return;
  if(project?.id===id)drafted=false;
  try{localStorage.removeItem(draftKey(id));}catch{}
});
const refreshHistory = setupHistory(() => project?.id);
addEventListener('beforeunload', saveDraft);
addEventListener('focus', () => {
  if (project && !commandsPending) pollProject(project.id, routeToken);
});
addEventListener('hashchange', openRoute);
openRoute();
