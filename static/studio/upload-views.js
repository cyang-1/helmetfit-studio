export const views = [
  ['front', '正面'],
  ['left', '左侧'],
  ['right', '右侧'],
  ['back', '背面'],
  ['top', '顶部'],
  ['bottom', '底部'],
  ['front_left', '左前斜视'],
  ['front_right', '右前斜视']
];
const titles = new Map([...views, ['model', '头盔模型'], ['reference', '参考图']]);
const imageAccept = 'image/png,image/jpeg,image/webp';
const transferType = 'application/x-helmetfit-reference-view';

function element(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function makeSlot(role, mode, file, thumb, actions) {
  const title = titles.get(role) || '未标注方向';
  const isPrimary = role === 'front' || role === 'model' || role === 'reference';
  const wrapper = element(
    'div',
    'angle-slot' + (isPrimary ? ' primary-angle' : '') + (file ? ' has-file' : '')
  );
  wrapper.dataset.view = role;
  const picker = element('label', 'angle-picker');
  const input = element('input', 'angle-input');
  input.type = 'file';
  input.accept = mode === 'model' ? '.stl,.obj,.glb' : imageAccept;
  input.setAttribute('aria-label', (file ? '更换' : '上传') + title);
  input.addEventListener('change', () => actions.assign(role, [...input.files]));
  const header = element('span', 'angle-header');
  header.append(element('strong', 'angle-title', title));
  if (isPrimary) header.append(element('span', 'angle-required', '必填'));
  picker.append(input, header);
  if (file) {
    if (thumb) {
      const image = element('img', 'angle-thumbnail');
      image.alt = title + '参考图';
      image.src = thumb;
      image.draggable = false;
      picker.append(image);
    }
    const name = element('span', 'angle-filename', file.name);
    name.title = file.name;
    picker.append(name);
  } else {
    picker.append(element('span', 'angle-plus', '+'));
    if (isPrimary)
      picker.append(
        element(
          'small',
          'angle-hint',
          mode === 'model'
            ? '选择或拖入模型文件'
            : mode === 'single'
              ? '选择或拖入一张设计图'
              : '选择或拖入正面图'
        )
      );
  }
  wrapper.append(picker);
  if (file) {
    const actionsRow = element('div', 'angle-tools');
    if (thumb && actions.preview) {
      const inspect = element('button', 'angle-inspect', '查看');
      inspect.type = 'button';
      inspect.setAttribute('aria-label', '查看' + title + '大图');
      inspect.addEventListener('click', () => actions.preview(file, thumb));
      actionsRow.append(inspect);
    }
    if (mode === 'multi') {
      const angle = element('select', 'angle-move');
      angle.setAttribute('aria-label', '调整' + title + '图片的视角');
      angle.add(new Option('调整角度', ''));
      for (const [id, label] of views) if (id !== role) angle.add(new Option('移至' + label, id));
      angle.addEventListener('change', () => actions.move(role, angle.value));
      actionsRow.append(angle);
      picker.draggable = true;
      picker.addEventListener('dragstart', (event) => {
        if (!event.dataTransfer) return;
        event.dataTransfer.setData(transferType, role);
        event.dataTransfer.effectAllowed = 'move';
      });
    }
    const remove = element('button', 'angle-remove', '移除');
    remove.type = 'button';
    remove.setAttribute('aria-label', '移除' + title + '文件');
    remove.addEventListener('click', () => actions.assign(role, []));
    actionsRow.append(remove);
    wrapper.append(actionsRow);
  }
  wrapper.addEventListener('dragover', (event) => {
    event.preventDefault();
    if (event.dataTransfer)
      event.dataTransfer.dropEffect = event.dataTransfer.types.includes(transferType) ? 'move' : 'copy';
    wrapper.classList.add('drag');
  });
  wrapper.addEventListener('dragleave', (event) => {
    if (!wrapper.contains(event.relatedTarget)) wrapper.classList.remove('drag');
  });
  wrapper.addEventListener('drop', (event) => {
    event.preventDefault();
    wrapper.classList.remove('drag');
    const previous = event.dataTransfer?.getData(transferType);
    if (previous && mode === 'multi') actions.move(previous, role);
    else if (event.dataTransfer?.files.length) actions.assign(role, [...event.dataTransfer.files]);
  });
  return wrapper;
}
