const form = document.getElementById('fit-form');
let openMask = false;
export const field = (name) => form.elements.namedItem(name);

function numberField(name, label, min, max, step, initial, unit = '') {
  const element = document.createElement('label');
  element.className = 'field';
  element.append(label);
  if (unit) {
    const hint = document.createElement('span');
    hint.textContent = unit;
    element.append(hint);
  }
  const input = document.createElement('input');
  Object.assign(input, {
    type: 'number',
    name,
    min,
    max,
    step,
    defaultValue: initial,
    required: initial !== ''
  });
  if (initial === '') input.placeholder = '选填';
  element.append(input);
  return element;
}
for (const [name, label, min, max] of [
  ['width', '头宽', 90, 230],
  ['length', '头长', 125, 290],
  ['height', '头高', 130, 330]
]) {
  document.getElementById('head-fields').append(numberField(name, label, min, max, 0.1, ''));
}
for (const [axis, group, min, max, step] of [
  ['r', '旋转（°）', -180, 180, 'any'],
  ['d', '位移（mm）', -300, 300, 'any']
]) {
  const wrapper = document.createElement('div');
  wrapper.className = 'pose-group';
  const label = document.createElement('span');
  label.textContent = group;
  wrapper.append(label);
  const row = document.createElement('div');
  row.className = 'field-row triple';
  for (const suffix of ['x', 'y', 'z'])
    row.append(numberField(axis + suffix, suffix.toUpperCase(), min, max, step, 0));
  wrapper.append(row);
  document.getElementById('pose-fields').append(wrapper);
}
for (const [name, label, min, max, step, initial] of [
  ['eye_width', '宽 mm', 40, 175, 0.1, 100],
  ['eye_height', '高 mm', 6, 80, 0.1, 26],
  ['eye_z', '高度比例', 0.25, 0.85, 0.01, 0.55]
]) {
  document.getElementById('eye-fields').append(numberField(name, label, min, max, step, initial));
}
export const poseKeys = ['scale', 'rx', 'ry', 'rz', 'dx', 'dy', 'dz'];
export const sizeKeys = ['circumference', 'width', 'length', 'height', 'clearance'];
export const headKeys = [...sizeKeys, 'dz'];
export function values() {
  const params = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.disabled && el.name !== 'eye_window') continue;
    if (el.type === 'checkbox') params[el.name] = el.checked;
    else if (el.type === 'number') {
      if (el.value !== '' && Number.isFinite(el.valueAsNumber)) params[el.name] = el.valueAsNumber;
    } else params[el.name] = el.value;
  }
  return params;
}
export function populate(params, reset = false) {
  if (reset) {
    form.reset();
    if (openMask) field('eye_window').checked = false;
  }
  for (const [key, val] of Object.entries(params || {})) {
    const f = field(key);
    if (!f) continue;
    if (f.type === 'checkbox') f.checked = Boolean(val);
    else f.value = val ?? '';
  }
  refreshEyeFields();
}
export function setFitStructure(value) {
  openMask = value === 'cap_mask';
  refreshEyeFields();
}
export function refreshEyeFields() {
  const cut = field('eye_window');
  document.getElementById('eye-cut-label').textContent = openMask ? '补开双眼视孔' : '切出贯通眼窗';
  document.getElementById('eye-options-title').textContent = openMask
    ? '眼线与试戴截面'
    : '眼窗和试戴截面';
  document.getElementById('open-mask-eyes').hidden = !openMask;
  document.getElementById('open-mask-eyes').textContent = cut.checked
    ? '沿眼线分别检查左右眼位，必要时补开椭圆视孔。'
    : '保留面具原有眼孔。调整眼线高度，检查视线能否穿过眼孔。';
  const fields = document.getElementById('eye-fields');
  fields.hidden = !openMask && !cut.checked;
  fields.classList.toggle('line-only', openMask);
  for (const input of fields.querySelectorAll('input')) {
    const isLine = input.name === 'eye_z';
    input.disabled = openMask && !isLine;
    input.closest('label').hidden = input.disabled;
    input.required = !input.disabled && (openMask || cut.checked);
    if (isLine) input.closest('label').firstChild.textContent = openMask ? '眼线高度比例' : '高度比例';
  }
}
export function validateFit() {
  const invalid = [...form.elements].find((el) => el.willValidate && !el.validity.valid);
  if (invalid) {
    const parent = invalid.closest('details');
    if (parent) parent.open = true;
    invalid.reportValidity();
    return false;
  }
  return true;
}
