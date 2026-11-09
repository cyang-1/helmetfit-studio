import { artifactURL } from './api.js';
import { canFit } from './workspace.js';

const $ = (id) => document.getElementById(id);

function checkList(container, items, kind) {
  if (!Array.isArray(items) || !items.length) return;
  const ul = document.createElement('ul');
  ul.className = 'checks-list ' + kind;
  for (const item of items) {
    const li = document.createElement('li');
    li.textContent = typeof item === 'string' ? item : item.message || item.label || '';
    if (li.textContent) ul.append(li);
  }
  container.append(ul);
}
export function renderOutputFiles(project, output, versions, drafted, busy) {
  const assets = output.assets || {},
    list = $('download-list'),
    checks = $('check-messages');
  list.replaceChildren();
  checks.replaceChildren();
  function download(label, url, button = '下载', stats = null) {
    const href = artifactURL(url);
    if (!href) return;
    const row = document.createElement('div');
    row.className = 'download-row';
    const name = document.createElement('span');
    name.textContent = label;
    if (stats) {
      const facts = [];
      if (stats.layers > 0) facts.push(stats.layers + ' 层');
      if (stats.grams > 0) facts.push(Number(stats.grams).toFixed(1) + ' g');
      if (stats.seconds > 0) {
        const mins = Math.ceil(stats.seconds / 60);
        facts.push('约 ' + (mins >= 60 ? Math.floor(mins / 60) + ' 小时 ' : '') + (mins % 60) + ' 分钟');
      }
      if (facts.length) {
        const details = document.createElement('small');
        details.textContent = facts.join(' · ');
        name.append(details);
      }
    }
    const link = document.createElement('a');
    link.className = 'text-link';
    link.href = href;
    link.download = '';
    link.textContent = button;
    row.append(name, link);
    list.append(row);
  }
  const select = $('output-revision');
  select.replaceChildren();
  versions.forEach((rev, index) => {
    const opt = document.createElement('option');
    opt.value = index === 0 ? 'current' : 'revision-' + rev.revision;
    opt.textContent = index === 0 ? '当前版本' : '历史版本 ' + rev.revision;
    select.append(opt);
  });
  select.value = output === project ? 'current' : 'revision-' + output.revision;
  select.hidden = versions.length <= 1;
  const blocking = output.report?.blocking || [];
  download('原始外形 · GLB', project.assets?.glb);
  const sampleName = output.report?.ring_kind === 'open_band' ? '开放试戴带' : '试戴环';
  download(sampleName + ' · P1S 3MF', assets.ring3mf, '下载', output.report?.ring_slice);
  if (!blocking.length) {
    for (const [index, plate] of (assets.plates || []).entries()) {
      download(plate.name || '头盔 · 第 ' + (index + 1) + ' 盘', plate.url, '下载', plate);
    }
    download('全部打印文件与装配说明', assets.package, '下载整包');
  }
  if (
    (project.stage === 'done' && output === project) ||
    (output !== project && assets.plates?.length)
  ) {
    const row = document.createElement('div');
    row.className = 'download-row';
    row.append(output === project ? '本次制作记录' : '版本 ' + output.revision + ' 制作记录');
    const link = document.createElement('a');
    link.className = 'text-link';
    link.href =
      '/api/projects/' +
      project.id +
      '/report' +
      (output === project ? '' : '?revision=' + encodeURIComponent(output.revision));
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = '查看 / 保存 PDF';
    row.append(link);
    list.append(row);
  }
  checkList(checks, blocking, 'blocked');
  checkList(checks, output.report?.warnings, '');
  $('outputs').hidden = !list.childElementCount && !checks.childElementCount && versions.length === 1;
  const historical = output !== project;
  $('restore-version').hidden = !historical || busy || !canFit(project);
  $('preview-version').hidden = !historical || !assets.fitted;
  const result = output.report?.checks;
  const readout = $('print-readout');
  readout.replaceChildren();
  $('output-checks').hidden = !result;
  if (result) {
    const metrics = [
      ['外壳尺寸', result.dimensions_mm?.map((v) => Number(v).toFixed(1)).join(' × ') + ' mm'],
      [
        '抽样最薄处',
        Number.isFinite(result.sampled_wall_min_mm) ? result.sampled_wall_min_mm + ' mm' : '未测得'
      ],
      ['戴入碰撞抽样', result.insertion_collision_samples + ' / ' + result.insertion_samples],
      ['双眼视线', result.eye_open ? '已贯通' : '待调整']
    ];
    for (const [label, value] of metrics) {
      const item = document.createElement('div');
      const title = document.createElement('small');
      title.textContent = label;
      item.append(title, value);
      readout.append(item);
    }
  }
  const sliced = (assets.plates?.length || 0) > 0;
  $('output-note').textContent = historical
    ? '正在查看历史版本 ' + output.revision + ' 的文件。'
    : drafted
      ? '尺寸有未提交的修改，现有文件使用上次提交的参数。'
      : sliced && !blocking.length
        ? '已生成 P1S 切片文件。打印整件前可先检查' + sampleName + '。'
        : '';
}
