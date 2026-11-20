function attachStoryEditor({current, apply, openSource, requestJson, assetName}) {
  const get = (id) => field(`story-${id}`);
  const labels = {dialogue: '台词', menu: '分支', jump: '跳转', return: '结束', media: '演出', stop: '音乐', source: '源码', transition: '演出'};
  const mediaLabels = {background: '背景', portrait: '立绘', music: '音乐', sound: '音效', voice: '配音'};
  const state = () => ({scenes: [], scene: '', rows: [], characters: {}, diagnostics: []});
  let data = state();
  let selected = null;
  let requestId = 0;
  let loaded;
  const rows = get('rows');
  const status = get('status');

  function element(tag, text = '', className = '') {
    const value = document.createElement(tag);
    value.textContent = text;
    value.className = className;
    return value;
  }

  function button(text, click) {
    const control = element('button', text);
    control.type = 'button';
    control.addEventListener('click', click);
    return control;
  }

  function input(label, control) {
    const wrapper = element('label', label);
    wrapper.append(control);
    return wrapper;
  }

  function selectOptions(items, value = '') {
    const select = element('select');
    select.append(...items.map(([id, label]) => new Option(label, id)));
    if (value) select.value = value;
    return select;
  }

  function selectRow(row) {
    selected = row?.line ?? null;
    for (const item of rows.children) item.dataset.selected = String(Number(item.dataset.line) === selected);
    get('insertion').textContent = selected ? `在第 ${selected} 行之前插入` : '加到本场末尾';
  }

  async function change(operation, details) {
    const previous = selected;
    if (operation === 'insert') selected = null;
    if (await apply({scene: data.scene, operation, ...details})) get('form').replaceChildren();
    else selected = previous;
  }

  function form(kind, row) {
    const fields = element('form', '', 'story-row-form');
    fields.append(element('strong', labels[kind]));
    let values = () => ({});
    const targets = (value) => selectOptions(data.scenes.map((scene) => [scene.name, scene.name]), value);
    if (kind === 'dialogue') {
      const voices = [['', '旁白'], ...Object.entries(data.characters)];
      if (row?.speaker && !Object.hasOwn(data.characters, row.speaker)) voices.push([row.speaker, row.speaker]);
      const speaker = selectOptions(voices, row?.speaker);
      const content = element('textarea');
      content.rows = 3;
      content.value = row?.content || '';
      content.required = true;
      fields.append(input('说话人', speaker), input('文本', content));
      values = () => ({speaker: speaker.value, content: content.value});
    } else if (kind === 'jump') {
      const target = targets(row?.target);
      target.required = true;
      fields.append(input('目标场景', target));
      values = () => ({target: target.value});
    } else if (kind === 'menu') {
      const choices = element('div');
      function choice(option = {}) {
        const item = element('div', '', 'story-choice');
        const title = element('input');
        title.value = option.content || '';
        title.required = true;
        const target = targets(option.target);
        target.required = true;
        item.append(input('选项文字', title), input('目标场景', target), button('移除', () => {
          if (choices.children.length > 1) item.remove();
        }));
        choices.append(item);
      }
      (row?.options || [{}, {}]).forEach(choice);
      fields.append(choices, button('添加选项', () => { if (choices.children.length < 12) choice(); }));
      values = () => ({options: [...choices.children].map((item) => ({
        content: item.querySelector('input').value, target: item.querySelector('select').value,
      }))});
    }
    const actions = element('div', '', 'story-actions');
    const submit = element('button', row ? '应用修改' : '插入命令', 'primary');
    submit.type = 'submit';
    actions.append(submit, button('取消', () => fields.remove()));
    fields.append(actions);
    fields.addEventListener('submit', async (event) => {
      event.preventDefault();
      submit.disabled = true;
      await change(row ? 'replace' : 'insert', {line: row?.line ?? selected ?? undefined, row: {kind, ...values()}});
      submit.disabled = false;
    });
    return fields;
  }

  function draw() {
    get('scene').replaceChildren(...data.scenes.map(({name}) => new Option(name, name)));
    get('scene').value = data.scene;
    get('play').disabled = !data.scene;
    get('add').disabled = !data.scene;
    rows.replaceChildren(...data.rows.map((row) => {
      const item = element('li', '', 'story-row');
      item.dataset.line = String(row.line);
      const content = element('div');
      const kind = row.kind === 'dialogue' ? data.characters[row.speaker] || row.speaker || '旁白'
        : row.kind === 'media' ? mediaLabels[row.action] : labels[row.kind];
      let description = row.target || row.content || (row.kind === 'stop' ? '停止音乐' : '返回');
      if (row.kind === 'menu') description = row.options.map((option) => `${option.content} → ${option.target}`).join('\n');
      if (row.kind === 'media') description = assetName(row.path) || row.path.split('/').pop();
      content.append(element('strong', kind), element('p', description));
      const actions = element('div', '', 'story-actions');
      const editor = element('div');
      actions.append(button('定位', () => selectRow(row)));
      if (['source', 'media', 'transition'].includes(row.kind)) actions.append(button('源码', () => openSource(row.line)));
      else actions.append(button('编辑', () => {
        rows.querySelector('.story-row-form')?.remove();
        selectRow(row);
        editor.append(form(row.kind, row));
        editor.querySelector('input, textarea, select, button')?.focus();
      }));
      if (row.kind !== 'source') actions.append(button('删除', () => change('delete', {line: row.line})));
      item.append(element('span', String(row.line), 'story-line'), content, actions, editor);
      return item;
    }));
    status.textContent = data.diagnostics.join(' ') || (!data.scene ? '从 Scenario 生成脚本，或先新建入口场景 start。' : '');
    selectRow(data.rows.find((row) => row.line === selected) || data.rows.find((row) => row.kind === 'return' || row.kind === 'jump'));
  }

  async function refresh(preferred = data.scene, focus) {
    const scope = current();
    if (!scope || (loaded?.project === scope.project && loaded.text === scope.text && loaded.scene === preferred && !focus)) return;
    const token = ++requestId;
    status.textContent = '正在读取剧情行…';
    try {
      const result = await requestJson('/api/script/story', {text: scope.text, scene: preferred || undefined});
      if (token !== requestId || scope.project !== current()?.project || scope.text !== current()?.text) return;
      data = result;
      loaded = {...scope, scene: data.scene};
      if (focus) selected = focus;
      draw();
    } catch (error) {
      if (token === requestId) status.textContent = error.message;
    }
  }

  const dialog = get('preview');
  const output = get('preview-output');
  const layers = ['bg', 'actor'].map((name) => get(`preview-${name}`));
  const tracks = ['music', 'sound', 'voice'].map((name) => get(`preview-${name}`));
  let playId = 0;

  function resetPreview() {
    ++playId;
    layers.forEach((layer) => layer.replaceChildren());
    for (const track of tracks) {
      track.pause();
      track.removeAttribute('src');
    }
  }

  function startPreview() {
    resetPreview();
    const token = playId;
    const scope = current();
    let segment = data;
    let index = 0;
    dialog.showModal();

    function finish(message) {
      output.replaceChildren(element('p', message));
    }

    async function next(target) {
      for (const control of output.querySelectorAll('button')) control.disabled = true;
      for (let steps = 0; steps < 200 && token === playId; steps++) {
        if (target) {
          try {
            segment = await requestJson('/api/script/story', {text: scope.text, scene: target});
          } catch (error) {
            if (token === playId) finish(error.message);
            return;
          }
          if (token !== playId) return;
          if (!segment.scene) return finish(`找不到场景 ${target}。`);
          index = 0;
          target = '';
        }
        const row = segment.rows[index++];
        if (!row) {
          const sceneIndex = segment.scenes.findIndex((scene) => scene.name === segment.scene);
          target = segment.scenes[sceneIndex + 1]?.name;
          if (!target) return finish('剧情结束。');
        } else if (row.kind === 'return') return finish('剧情结束。');
        else if (row.kind === 'jump') target = row.target;
        else if (row.kind === 'menu') {
          output.replaceChildren(...row.options.map((option) => button(option.content, () => next(option.target))));
          return;
        } else if (row.kind === 'dialogue') {
          const name = segment.characters[row.speaker] || row.speaker;
          output.replaceChildren(element('strong', name || '旁白'), element('p', row.content), button('继续', () => next()));
          return;
        } else if (row.kind === 'stop') tracks[0].pause();
        else if (row.kind === 'source') return finish(`试玩停在第 ${row.line} 行。请用 Ren’Py SDK 运行这段源码。`);
        else if (row.kind === 'media') {
          const asset = scope.assets[row.path];
          if (!asset) return finish(`第 ${row.line} 行的素材尚未登记。`);
          const url = `/api/workspace/assets/${encodeURIComponent(asset)}?preview=1`;
          if (['background', 'portrait'].includes(row.action)) {
            const image = element('img');
            image.alt = '';
            image.src = url;
            image.addEventListener('error', () => image.remove(), {once: true});
            layers[row.action === 'background' ? 0 : 1].replaceChildren(image);
          } else {
            const player = get(`preview-${row.action}`);
            player.src = url;
            player.play().catch(() => {});
          }
        }
      }
      if (token === playId) finish('跳转次数过多，请检查场景之间是否形成循环。');
    }
    next();
  }

  get('scene').addEventListener('change', () => {
    selected = null;
    get('form').replaceChildren();
    refresh(get('scene').value);
  });
  get('new-scene').addEventListener('submit', async (event) => {
    event.preventDefault();
    const name = get('new-name').value.trim() || (data.scenes.length ? '' : 'start');
    if (await apply({operation: 'add_scene', scene: name})) get('new-name').value = '';
  });
  get('new-character').addEventListener('submit', async (event) => {
    event.preventDefault();
    const row = {speaker: get('character-id').value.trim(), content: get('character-name').value.trim()};
    if (await apply({operation: 'add_character', scene: data.scene, row})) event.target.reset();
  });
  get('add').addEventListener('click', () => {
    rows.querySelector('.story-row-form')?.remove();
    get('form').replaceChildren(form(get('command').value));
    get('form').querySelector('input, textarea, select, button')?.focus();
  });
  get('play').addEventListener('click', startPreview);
  dialog.addEventListener('close', resetPreview);
  return {refresh, insertAsset: (asset, action) => change('insert', {line: selected ?? undefined,
    row: {kind: 'media', asset_id: asset, action}}), reset() {
    ++requestId;
    loaded = null;
    data = state();
    selected = null;
    get('form').replaceChildren();
    resetPreview();
    if (dialog.open) dialog.close();
    draw();
  }};
}
