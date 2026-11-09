import { structureField } from './wear-structure.js';

const themeLimit = 500;

const presets = [
  ['character', '角色头盔', '将角色的配色、帽饰与表情特征融入盔体。'],
  ['mecha', '机甲头盔', '用装甲分片、面罩和机械棱线塑造轮廓。'],
  ['creature', '生物头盔', '把角、鳞片或耳部特征与头盔外壳连成一体。']
];

export function createTextFields(onChange) {
  const panel = document.createElement('div');
  panel.className = 'text-source';
  panel.innerHTML = `
    <label class="field" for="helmet-theme">头盔主题</label>
    <textarea id="helmet-theme" rows="3" placeholder="例如：圣诞老人，红色帽沿、银白色胡须护颌"
      aria-describedby="source-note theme-count" required></textarea>
    <div id="theme-count" class="theme-count"></div>
    <fieldset class="theme-presets">
      <legend>造型方向</legend><div class="theme-options"></div>
      <p class="preset-hint"></p>
    </fieldset>`;
  const theme = panel.querySelector('textarea');
  const options = panel.querySelector('.theme-options');
  const count = panel.querySelector('.theme-count');
  const hint = panel.querySelector('.preset-hint');
  const structure = structureField('helmet-structure', '佩戴结构', true);
  panel.append(structure.group);
  for (const [value, label] of presets) {
    const choice = document.createElement('label');
    const radio = document.createElement('input');
    Object.assign(radio, {
      type: 'radio',
      name: 'helmet-preset',
      value,
      checked: value === 'character'
    });
    const title = document.createElement('span');
    title.textContent = label;
    choice.append(radio, title);
    options.append(choice);
  }
  function values() {
    return {
      theme: theme.value,
      preset: options.querySelector(':checked').value,
      structure: structure.select.value
    };
  }
  function length() {
    return Array.from(theme.value.trim()).length;
  }
  function refresh() {
    const size = length();
    const overflow = size > themeLimit;
    count.textContent = overflow
      ? `已超出 ${size - themeLimit} 字，请缩短主题。`
      : `${size} / ${themeLimit}`;
    theme.setCustomValidity(overflow ? count.textContent : '');
    theme.setAttribute('aria-invalid', String(overflow));
    count.classList.toggle('over-limit', overflow);
    hint.textContent = presets.find(([value]) => value === values().preset)[2];
  }
  function edited() {
    refresh();
    onChange();
  }
  theme.addEventListener('input', edited);
  options.addEventListener('change', edited);
  structure.select.addEventListener('change', edited);
  refresh();
  return {
    panel,
    values,
    restore(spec) {
      theme.value = spec?.theme || '';
      const radios = [...options.querySelectorAll('input')];
      (radios.find((radio) => radio.value === spec?.preset) || radios[0]).checked = true;
      structure.set(spec?.structure || 'auto');
      refresh();
    },
    valid: () => length() > 0 && length() <= themeLimit,
    example() {
      theme.value = '圣诞老人';
      edited();
      theme.focus();
    },
    reset() {
      theme.value = '';
      options.querySelector('[value="character"]').checked = true;
      structure.set('auto');
      refresh();
    }
  };
}
