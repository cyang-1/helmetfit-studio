const choices = {
  auto: ['按主题推荐', '由主题决定头顶、面部和后脑的覆盖范围。'],
  cap_mask: ['帽子＋面具（后脑开放）', '帽子覆盖头顶，面具覆盖脸部，耳后和后脑留空。'],
  full_shell: ['全包头盔', '外壳包覆头顶、两侧和后脑，从底部开口戴入。']
};

export function structureName(value) {
  return choices[value]?.[0] || choices.full_shell[0];
}

export function recordedStructure(project) {
  return project.prompt_spec?.resolved_structure || project.wear_structure || 'full_shell';
}

// Legacy text projects froze a full-shell prompt before the selector existed.
export function originalChoice(project) {
  return project.prompt_spec?.structure || 'full_shell';
}

export function structureField(id, label, allowAuto) {
  const group = document.createElement('div');
  group.className = 'structure-field';
  const caption = document.createElement('label');
  caption.className = 'field';
  caption.textContent = label;
  const select = document.createElement('select');
  select.id = id;
  select.setAttribute('aria-describedby', id + '-hint');
  const hint = document.createElement('p');
  hint.id = id + '-hint';
  hint.className = 'muted';
  for (const key of allowAuto ? ['auto', 'cap_mask', 'full_shell'] : ['cap_mask', 'full_shell']) {
    select.add(new Option(choices[key][0], key));
  }
  caption.append(select);
  group.append(caption, hint);
  function describe() {
    hint.textContent = choices[select.value][1];
  }
  function set(value) {
    select.value = value;
    describe();
  }
  function load(projectId, value) {
    // Polling the workflow must not overwrite a pending choice in the selector.
    if (select.dataset.project !== projectId || select.dataset.saved !== value) {
      set(value);
      select.dataset.project = projectId;
      select.dataset.saved = value;
    }
  }
  select.addEventListener('change', describe);
  describe();
  return { group, select, set, load };
}
