function attachScriptWorkspace({get, pane, active, api, element, button, operate, applyDraft, openFile, notice}) {
  const nav = [...pane.querySelectorAll('[data-ide-view]')];
  const screenFile = 'prisma_ui.rpy';
  const variableFile = 'prisma_variables.rpy';
  const variantFile = 'prisma-variants.json';
  const edit = get('edit-dialog');
  const rowKinds = {dialogue: '台词', jump: '跳转', call: '调用场景', screen: '界面命令', pause: '等待', menu: '选项', media: '演出素材',
    stop: '停止音乐', return: '结束场景', transition: '过渡', source: '源码语句'};
  const callouts = {added: '新增', changed: '修改', deleted: '删除'};
  const targetActions = ['jump', 'show', 'hide', 'set', 'toggle', 'language'];
  let view = 'source';
  let ticket = 0;
  let scene;
  let story = {scenes: [], characters: {}, rows: [], branches: [], diagnostics: []};
  let screen = '';
  let screens = {};
  let variables = {};
  let widget = -1;
  let history = [];
  let nextRevision = '';
  let revision = '';
  let editAction;
  let variants = {};

  function fieldLabel(text, field) {
    const label = element('label', text);
    label.append(field);
    return label;
  }

  function input(value = '', options = {}) {
    return Object.assign(element('input'), {value, ...options});
  }

  function choices(items, value) {
    const select = element('select');
    select.replaceChildren(...items.map(([id, title]) => new Option(title, id)));
    if (value !== undefined) select.value = value;
    return select;
  }

  function editForm(title, fields, action) {
    get('edit-title').textContent = title;
    get('edit-fields').replaceChildren(...fields);
    get('edit-error').hidden = true;
    get('edit-form').querySelector('[type=submit]').disabled = false;
    editAction = action;
    edit.showModal();
  }

  function sync() {
    const buffer = active();
    const busy = !buffer || buffer.busy;
    for (const id of ['scene-add', 'character-add', 'screen-add', 'variable-add', 'history-refresh', 'history-more', 'variant-add']) get(id).disabled = busy;
    for (const id of ['scene-source', 'row-add']) get(id).disabled = busy || !scene?.name;
    get('screen-source').disabled = busy || !buffer?.files[screenFile];
    get('widget-add').disabled = busy || !screen;
    get('history-restore').disabled = busy || !revision;
    get('localized-add').disabled = busy || !Object.keys(buffer.managed_assets).length
      && !Object.keys(buffer.project_assets).some((path) => !/^tl\//i.test(path));
  }

  function jumpToSource(file, line) {
    show('source');
    openFile(file, line);
  }

  function drawStory() {
    const scenes = get('scene-list');
    scenes.replaceChildren(...story.scenes.map((entry) => {
      const control = button(entry.name, () => { scene = entry; show('story'); });
      control.title = entry.file + ':' + entry.line;
      control.setAttribute('aria-pressed', String(entry.file === scene?.file && entry.name === scene?.name));
      return control;
    }));
    get('character-list').replaceChildren(...Object.entries(story.characters).map(([id, name]) => {
      const entry = element('li', name);
      entry.append(element('code', id));
      return entry;
    }));
    get('scene-title').textContent = scene?.name || '场景';
    get('scene-path').textContent = scene ? 'game/' + scene.file + ':' + scene.line : '';
    get('story-status').textContent = story.diagnostics.join(' ') || (!scene ? '新建一个场景，或从 Scenario 生成工程。' : '');
    get('story-rows').replaceChildren(...story.rows.map((row, index) => {
      const item = element('li', '', 'ide-story-row');
      const text = element('div', '', 'ide-story-text');
      const name = story.characters[row.speaker] || row.speaker || '旁白';
      text.append(element('strong', row.kind === 'dialogue' ? name : rowKinds[row.kind]));
      const value = row.target || (row.kind === 'media' ? row.path : row.content);
      if (value !== undefined) text.append(element('p', String(value)));
      if (row.options) {
        const choices = element('ul');
        for (const choice of row.options) choices.append(element('li', choice.content + ' → ' + choice.target));
        text.append(choices);
      }
      item.append(element('span', String(row.line), 'ide-line-number'), text);
      const actions = element('div', '', 'ide-tools ide-row-actions');
      actions.append(button('源码', () => jumpToSource(scene.file, row.line), '打开第 ' + row.line + ' 行源码'),
        button('前插', () => rowForm(row, 'insert'), '在第 ' + row.line + ' 行前插入'));
      if (row.kind !== 'source') {
        actions.append(button('编辑', () => rowForm(row, 'replace'), '编辑第 ' + row.line + ' 行'),
          button('删除', () => {
            editForm('删除第 ' + row.line + ' 行？', [element('p', value || rowKinds[row.kind])],
              () => changeStory({operation: 'delete', file: scene.file, scene: scene.name, line: row.line}));
          }, '删除第 ' + row.line + ' 行'));
      }
      if (row.kind !== 'source') {
        for (const [direction, delta, label] of [['move_up', -1, '上移'], ['move_down', 1, '下移']]) {
          const neighbor = story.rows[index + delta];
          if (neighbor && neighbor.kind !== 'source') actions.append(button(label, async () => {
            if (await changeStory({operation: direction, file: scene.file, scene: scene.name, line: row.line})) show('story');
          }, label + '第 ' + row.line + ' 行'));
        }
      }
      item.append(actions);
      return item;
    }));
    sync();
  }

  function drawFlow() {
    const resolved = new Map(story.scenes.map((entry) => [entry.name, entry]));
    const outgoing = new Map();
    for (const branch of story.branches) {
      const key = branch.file + '/' + branch.source;
      if (!outgoing.has(key)) outgoing.set(key, []);
      outgoing.get(key).push(branch);
    }
    get('flow-status').textContent = story.scenes.length
      ? '显示源码中的静态跳转、选项和同文件顺序衔接。点击场景进入编辑。' : '工程中还没有场景。';
    get('flow').replaceChildren(...story.scenes.map((entry) => {
      const row = element('li', '', 'ide-flow-row');
      const source = button(entry.name, () => { scene = entry; show('story'); });
      source.title = entry.file + ':' + entry.line;
      const nodes = element('ul');
      for (const branch of outgoing.get(entry.file + '/' + entry.name) || []) {
        const edge = element('li');
        const target = resolved.get(branch.target);
        const action = {next: '顺序', call: '调用场景', jump: '跳转'};
        const name = (branch.content || action[branch.kind]) + ' → ' + branch.target;
        if (target) edge.append(button(name, () => { scene = target; show('story'); }));
        else edge.append(element('span', name + '（未找到）', 'error-text'));
        nodes.append(edge);
      }
      if (!nodes.childElementCount) nodes.append(element('li', '无静态去向', 'hint'));
      row.append(source, nodes);
      return row;
    }));
  }

  async function readStory(stamp, buffer) {
    const result = await api('/api/script/editor/story', {files: buffer.files, file: scene?.file, scene: scene?.name});
    if (stamp !== ticket || buffer !== active()) return;
    story = result;
    scene = story.scenes.find((entry) => entry.file === story.file && entry.name === story.scene);
    if (view === 'story') drawStory();
    else drawFlow();
  }

  async function changeStory(data) {
    return operate(async (project, buffer) => {
      const file = data.file;
      const files = Object.hasOwn(buffer.files, file) ? buffer.files : {...buffer.files, [file]: ''};
      const result = await api('/api/script/story/edit', {...data, project_id: project, files});
      applyDraft(buffer, {files: {...files, [file]: result.text},
        managed_assets: {...buffer.managed_assets, ...result.managed_assets}}, file);
      if (buffer === active()) scene = {file, name: result.scene};
    }, edit.open ? get('edit-error') : undefined);
  }

  function addNamed(kind) {
    const character = kind === 'character';
    const id = input('', {required: true, maxLength: 64, pattern: '[A-Za-z_][A-Za-z_0-9]*'});
    const name = input('', {required: true, maxLength: 120});
    const locations = Object.keys(active().files).filter((file) => /\.rpy$/i.test(file)).sort();
    if (!locations.length) locations.push('script.rpy');
    const file = choices(locations.map((path) => [path, 'game/' + path]), locations.includes(scene?.file) ? scene.file : locations[0]);
    const fields = [fieldLabel(character ? '角色标识' : '场景标签', id)];
    if (character) fields.push(fieldLabel('显示名称', name));
    fields.push(fieldLabel('保存到', file));
    editForm(character ? '新建角色' : '新建场景', fields, () => {
      if (!character && story.scenes.some((entry) => entry.name === id.value)) {
        throw new Error('工程已存在此场景。');
      }
      return changeStory({operation: character ? 'add_character' : 'add_scene', file: file.value,
        scene: character ? scene?.name || '' : id.value, row: {speaker: id.value, content: name.value}});
    });
  }

  function rowForm(original, operation) {
    const row = operation === 'replace' ? original : {};
    const list = ['dialogue', 'jump', 'call', 'menu', 'media', 'screen', 'pause', 'transition', 'stop', 'return'];
    const kind = choices(list.map((id) => [id, rowKinds[id]]), row.kind || 'dialogue');
    const details = element('div', '', 'ide-edit-details');
    const destinations = [...new Map(story.scenes.map((entry) => [entry.name, entry.name]))];
    let readRow;
    function configure() {
      details.replaceChildren();
      const type = kind.value;
      if (type === 'dialogue') {
        const speaker = choices([['', '旁白'], ...Object.entries(story.characters)], row.speaker || '');
        if (row.speaker && !Object.hasOwn(story.characters, row.speaker)) {
          speaker.add(new Option(row.speaker, row.speaker, true, true));
        }
        const content = Object.assign(element('textarea'), {value: row.content || '', required: true, rows: 4, maxLength: 10000});
        details.append(fieldLabel('角色', speaker), fieldLabel('台词', content));
        readRow = () => ({speaker: speaker.value, content: content.value});
      } else if (type === 'jump' || type === 'call') {
        const target = choices(destinations, row.target);
        target.required = true;
        details.append(fieldLabel('目标场景', target));
        readRow = () => ({target: target.value});
      } else if (type === 'screen') {
        const action = choices([['show', '显示'], ['hide', '隐藏'], ['call', '调用并等待返回']], row.action);
        const target = input(row.target || '', {required: true, pattern: '[A-Za-z_][A-Za-z_0-9]*'});
        details.append(fieldLabel('界面动作', action), fieldLabel('界面名称', target));
        readRow = () => ({action: action.value, target: target.value});
      } else if (type === 'pause') {
        const time = input(row.content ?? '1', {type: 'number', min: '0', max: '600', step: '0.001', required: true});
        details.append(fieldLabel('秒数', time));
        readRow = () => ({content: time.valueAsNumber});
      } else if (type === 'transition') {
        const effect = choices([['with dissolve', '溶解'], ['with fade', '淡入淡出'], ['with None', '立即切换'],
          ['pass', '空语句']], row.content);
        details.append(fieldLabel('过渡效果', effect));
        readRow = () => ({content: effect.value});
      } else if (type === 'menu') {
        const options = element('div', '', 'ide-edit-details');
        function add(option = {}) {
          const pair = element('div', '', 'ide-choice-edit');
          const caption = input(option.content, {required: true});
          const target = choices(destinations, option.target);
          target.required = true;
          pair.append(fieldLabel('选项文本', caption), fieldLabel('目标场景', target),
            button('移除', () => { if (options.childElementCount > 1) pair.remove(); }));
          options.append(pair);
        }
        for (const option of row.options || [{}, {}]) add(option);
        details.append(options, button('添加选项', () => { if (options.childElementCount < 12) add(); }));
        readRow = () => ({options: [...options.children].map((pair) => ({
          content: pair.querySelector('input').value, target: pair.querySelector('select').value,
        }))});
      } else if (type === 'media') {
        const assets = [...get('asset').options].filter((option) => option.dataset.id && !option.dataset.path);
        const mediaActions = [['background', '背景'], ['portrait', '立绘'], ['music', '音乐'], ['sound', '音效'], ['voice', '配音']];
        const action = choices(mediaActions, row.action);
        const asset = element('select');
        asset.required = true;
        function filterAssets() {
          const images = ['background', 'portrait'].includes(action.value);
          const extension = images ? /\.(png|jpe?g|webp)$/i : /\.(ogg|opus|mp3|wav|flac)$/i;
          asset.replaceChildren(new Option('选择已通过素材', ''), ...assets.filter((item) => extension.test(item.textContent))
            .map((item) => new Option(item.textContent, item.dataset.id)));
          const existing = active()?.managed_assets[row.path];
          if (existing && [...asset.options].some((item) => item.value === existing)) asset.value = existing;
        }
        action.onchange = filterAssets;
        filterAssets();
        details.append(fieldLabel('演出动作', action), fieldLabel('素材', asset));
        readRow = () => ({action: action.value, asset_id: asset.value});
      } else readRow = () => ({});
    }
    kind.onchange = configure;
    configure();
    editForm(operation === 'replace' ? '编辑剧情行' : '添加剧情行', [fieldLabel('语句', kind), details], () => {
      const last = story.rows.at(-1);
      const line = original?.line || (last?.kind === 'return' ? last.line : undefined);
      return changeStory({operation, line, file: scene.file, scene: scene.name, row: {kind: kind.value, ...readRow()}});
    });
  }

  function drawScreens() {
    const canvas = get('screen-canvas');
    const selected = screens[screen];
    const config = document.getElementById('project-width');
    const width = Number(config?.value) || 1280;
    const height = Number(document.getElementById('project-height')?.value) || 720;
    canvas.style.aspectRatio = width + ' / ' + height;
    get('screen-list').replaceChildren(...Object.keys(screens).map((name) => {
      const control = button(name, () => { screen = name; widget = -1; drawScreens(); });
      control.setAttribute('aria-pressed', String(name === screen));
      return control;
    }));
    get('variable-list').replaceChildren(...Object.entries(variables).map(([name, value]) =>
      button(name + ' = ' + JSON.stringify(value), () => variableForm(name))));
    get('screen-title').textContent = screen || '界面';
    get('screen-status').textContent = screen ? width + ' × ' + height + ' · 控件从后到前绘制' : '新建界面或从工程模板开始。';
    const controls = selected || [];
    const labels = controls.map((control) => {
      if (control.type === 'panel') return '色块 ' + control.value;
      if (control.type === 'variable' && Object.hasOwn(variables, control.value)) return String(variables[control.value]);
      return control.value || '空白控件';
    });
    get('widget-layers').replaceChildren(...controls.map((_, index) => {
      const layer = button(labels[index], () => { widget = index; drawScreens(); });
      layer.setAttribute('aria-pressed', String(index === widget));
      return layer;
    }));
    canvas.replaceChildren(...controls.map((control, index) => {
      const name = labels[index];
      let moved = false;
      let drag;
      const item = button(name, () => {
        if (!moved) { widget = index; drawScreens(); }
      }, '选择控件 ' + name);
      Object.assign(item.style, {left: control.x + '%', top: control.y + '%', width: control.w + '%', height: control.h + '%'});
      item.dataset.widget = control.type;
      item.setAttribute('aria-pressed', String(index === widget));
      if (control.type === 'panel') {
        item.style.background = control.value;
        item.textContent = '';
      }
      if (control.type === 'image') {
        const buffer = active();
        const id = buffer.project_assets[control.value] || buffer.managed_assets[control.value];
        if (id) {
          const image = element('img');
          image.alt = '';
          image.draggable = false;
          image.src = '/api/workspace/assets/' + encodeURIComponent(id) + '?preview=1';
          item.replaceChildren(image);
        }
      }
      item.addEventListener('pointerdown', (event) => {
        if (event.button !== 0 || active()?.busy) return;
        const bounds = canvas.getBoundingClientRect();
        drag = {pointer: event.pointerId, pageX: event.clientX, pageY: event.clientY, bounds, x: control.x, y: control.y};
        moved = false;
        item.setPointerCapture(event.pointerId);
      });
      item.addEventListener('pointermove', (event) => {
        if (drag?.pointer !== event.pointerId) return;
        const dx = event.clientX - drag.pageX;
        const dy = event.clientY - drag.pageY;
        if (!moved && Math.abs(dx) + Math.abs(dy) < 4) return;
        moved = true;
        drag.x = Math.min(100 - control.w, Math.max(0, Math.round(control.x + dx * 100 / drag.bounds.width)));
        drag.y = Math.min(100 - control.h, Math.max(0, Math.round(control.y + dy * 100 / drag.bounds.height)));
        item.style.left = drag.x + '%';
        item.style.top = drag.y + '%';
      });
      item.addEventListener('pointercancel', () => {
        drag = undefined;
        item.style.left = control.x + '%';
        item.style.top = control.y + '%';
      });
      item.addEventListener('pointerup', async (event) => {
        if (drag?.pointer !== event.pointerId) return;
        const {x, y} = drag;
        drag = undefined;
        if (!moved) return;
        widget = index;
        const originalScreen = screen;
        await writeScreens({...screens, [screen]: controls.with(index, {...control, x, y})});
        if (screen === originalScreen) drawScreens();
      });
      return item;
    }));
    const data = selected?.[widget];
    get('widget-form').hidden = !data;
    if (data) {
      for (const key of ['type', 'value', 'x', 'y', 'w', 'h', 'action', 'target', 'argument', 'visible_when']) {
        get('widget-' + key).value = data[key] ?? '';
      }
      updateWidgetFields();
    }
    sync();
  }

  function updateWidgetFields() {
    const isButton = get('widget-type').value === 'button';
    const action = get('widget-action').value;
    get('widget-action-row').hidden = !isButton;
    get('widget-target-row').hidden = !isButton || !targetActions.includes(action);
    get('widget-target').required = !get('widget-target-row').hidden;
    get('widget-argument-row').hidden = !isButton || action !== 'set';
    const kind = get('widget-type').value;
    const placeholders = {panel: '#174a42', image: 'images/title.png', variable: '游戏变量标识', component: '要复用的界面名称'};
    get('widget-value').placeholder = placeholders[kind] || '';
  }

  async function writeScreens(next) {
    return operate(async (_, buffer) => {
      const response = await api('/api/script/editor/screens', {text: buffer.files[screenFile] || '', screens: next});
      applyDraft(buffer, {files: {...buffer.files, [response.file]: response.text}}, response.file);
      if (buffer === active()) screens = response.screens;
    }, edit.open ? get('edit-error') : undefined);
  }

  async function readScreens(stamp, buffer) {
    const [response, defaults] = await Promise.all([
      api('/api/script/editor/screens', {text: buffer.files[screenFile] || ''}),
      api('/api/script/editor/variables', {text: buffer.files[variableFile] || ''}),
    ]);
    if (stamp !== ticket || buffer !== active()) return;
    screens = response.screens;
    variables = defaults.variables;
    if (!Object.hasOwn(screens, screen)) screen = Object.keys(screens)[0] || '';
    drawScreens();
  }

  function variableForm(existing = '') {
    const name = input(existing, {required: true, readOnly: Boolean(existing), maxLength: 64, pattern: '[A-Za-z_][A-Za-z_0-9]*'});
    const value = input(existing ? JSON.stringify(variables[existing]) : 'false', {required: true, maxLength: 250});
    editForm(existing ? '编辑初始值' : '新建游戏变量',
      [fieldLabel('变量标识', name), fieldLabel('初始值（JSON 布尔值、数字或带引号的文本）', value)], () => {
        if (!existing && Object.hasOwn(variables, name.value)) throw new Error('已有同名变量。');
        let initial;
        try { initial = JSON.parse(value.value); }
        catch { throw new Error('请输入布尔值、数字或带双引号的文本。'); }
        return operate(async (_, buffer) => {
          const result = await api('/api/script/editor/variables', {text: buffer.files[variableFile] || '',
            variables: {...variables, [name.value]: initial}});
          applyDraft(buffer, {files: {...buffer.files, [result.file]: result.text}}, result.file);
        }, get('edit-error'));
      });
  }

  async function readVariants(stamp, buffer) {
    const data = await api('/api/script/editor/variants', {text: buffer.files[variantFile] || ''});
    if (stamp !== ticket || buffer !== active()) return;
    variants = data.variants;
    get('variant-status').textContent = '变体可在指定场景结束游戏，并排除选定文件。保存并通过后即可构建。';
    get('variant-list').replaceChildren(...Object.entries(variants).map(([id, config]) =>
      button(id + (config.stop_at ? ' · 截止于 ' + config.stop_at : ''), () => variantForm(id))));
  }

  async function variantForm(existing = '') {
    const buffer = active();
    const stamp = ticket;
    if (!buffer || buffer.busy) return;
    const data = await api('/api/script/editor/story', {files: buffer.files});
    if (buffer !== active() || stamp !== ticket) return;
    const variant = variants[existing];
    const id = input(existing, {required: true, readOnly: Boolean(existing), maxLength: 32, pattern: '[a-z][a-z0-9_]{0,31}'});
    const labels = [...new Set(data.scenes.map((entry) => entry.name))];
    const stop = choices([['', '无截止场景'], ...labels.map((name) => [name, name])], variant?.stop_at);
    const exclude = Object.assign(element('textarea'), {rows: 4, value: (variant?.exclude || []).join('\n'),
      placeholder: '每行一个相对 game/ 的路径，例如 chapters/bonus.rpy'});
    const fields = [fieldLabel('变体标识', id), fieldLabel('截止场景', stop), fieldLabel('排除路径', exclude)];
    editForm(existing ? '编辑发行变体' : '新建发行变体', fields, async () => {
      if (!existing && Object.hasOwn(variants, id.value)) throw new Error('已存在同名变体。');
      const next = {...variants, [id.value]: {stop_at: stop.value, exclude: exclude.value.split('\n').map((path) => path.trim()).filter(Boolean)}};
      return operate(async (_, current) => {
        const response = await api('/api/script/editor/variants', {variants: next});
        applyDraft(current, {files: {...current.files, [response.file]: response.text}}, response.file);
      }, get('edit-error'));
    });
  }

  function drawLocalization(buffer) {
    get('languages').replaceChildren(...Object.keys(buffer.files).filter((file) => /^tl\/.+\.rpy$/i.test(file)).sort().map((file) => {
      const row = element('li');
      row.append(button(file, () => jumpToSource(file, 1)));
      return row;
    }));
    get('localized-assets').replaceChildren(...Object.keys(buffer.project_assets).filter((file) => /^tl\//i.test(file)).sort().map((file) => {
      const row = element('li', '', 'ide-tools');
      row.append(element('code', file), button('移除', () => editForm('移除语言替换？', [element('p', file)], () =>
        operate(async (_, draft) => {
          const assets = {...draft.project_assets};
          delete assets[file];
          applyDraft(draft, {project_assets: assets});
        }, get('edit-error')))));
      return row;
    }));
  }

  function localizationForm() {
    const buffer = active();
    const paths = Object.keys({...buffer.project_assets, ...buffer.managed_assets}).filter((path) => !/^tl\//i.test(path)).sort();
    const base = choices(paths.map((path) => [path, path]));
    const language = input(get('language').value, {required: true, maxLength: 32, pattern: '[a-z][a-z0-9_]{0,31}'});
    const replacement = element('select');
    replacement.required = true;
    function selectMedia() {
      const extension = base.value.split('.').at(-1).toLowerCase();
      const candidates = [...get('asset').options].filter((item) => item.dataset.id && item.textContent.split('.').at(-1).toLowerCase() === extension);
      replacement.replaceChildren(new Option('选择同格式素材', ''), ...candidates.map((item) => new Option(item.textContent, item.dataset.id)));
    }
    selectMedia();
    base.onchange = selectMedia;
    editForm('添加语言素材', [fieldLabel('默认素材路径', base), fieldLabel('语言标识', language),
      fieldLabel('替换素材', replacement)], () => operate(async (project, draft) => {
      const result = await api('/api/script/editor/localized-asset',
        {project_id: project, language: language.value, path: base.value, asset_id: replacement.value});
      applyDraft(draft, {project_assets: {...draft.project_assets, [result.path]: result.asset_id}});
    }, get('edit-error')));
  }

  function drawHistory() {
    get('history-list').replaceChildren(...history.map((item) => {
      const control = button(new Date(item.updated).toLocaleString() + ' · ' + item.id.slice(0, 8), () => {
        revision = item.id;
        drawHistory();
        showDiff();
      });
      control.setAttribute('aria-pressed', String(item.id === revision));
      return control;
    }));
    get('history-more').hidden = !nextRevision;
    sync();
  }

  async function readHistory(stamp, buffer, append = false) {
    const data = await api('/api/script/editor/history', {project_id: openProject.id, ...(append ? {cursor: nextRevision} : {})});
    if (stamp !== ticket || buffer !== active()) return;
    history = append ? [...history, ...data.items] : data.items;
    nextRevision = data.next;
    if (!history.some((item) => item.id === revision)) revision = history[0]?.id || '';
    drawHistory();
    showDiff();
  }

  async function showDiff() {
    const host = get('diff-files');
    const status = get('diff-status');
    const selected = revision;
    const buffer = active();
    const project = openProject?.id;
    host.replaceChildren();
    status.textContent = selected ? '正在比较版本…' : '保存工程后，这里会显示完整的版本记录。';
    if (!selected || !project) return;
    const criteria = {project_id: project, revision: selected};
    try {
      const result = await api('/api/script/editor/diff', criteria);
      if (buffer !== active() || selected !== revision) return;
      status.textContent = (result.base ? result.base.slice(0, 8) + ' → ' : '初始版本 · ') + selected.slice(0, 8)
        + ' · ' + result.files.length + ' 个源文件，' + result.assets.length + ' 项素材变更';
      for (const file of result.files) {
        const section = element('details');
        const code = element('pre');
        section.append(element('summary', callouts[file.kind] + ' · ' + file.file), code);
        section.addEventListener('toggle', async () => {
          if (!section.open || code.textContent) return;
          code.textContent = '正在读取差异…';
          try {
            const detail = await api('/api/script/editor/diff', {...criteria, file: file.file});
            if (detail.too_large) code.textContent = '文件较大。请打开历史草稿查看完整源码。';
            else if (!detail.patch) code.textContent = '仅有文件结尾变化。';
            else code.replaceChildren(...detail.patch.split('\n').map((line) =>
              element('span', line + '\n', line.startsWith('+') ? 'ide-diff-added' : line.startsWith('-') ? 'ide-diff-deleted' : '')));
          } catch (error) { code.textContent = error.message; }
        });
        host.append(section);
      }
      for (const entry of result.assets) {
        host.append(element('p', entry.file + ' · ' + (entry.before || '无') + ' → ' + (entry.after || '无')));
      }
    } catch (error) {
      if (buffer === active() && selected === revision) status.textContent = error.message;
    }
  }

  async function merge() {
    const buffer = active();
    if (!buffer?.dirty) return;
    const request = {base: buffer.id, files: buffer.files, project_assets: buffer.project_assets, managed_assets: buffer.managed_assets};
    let conflicts = [];
    let head;
    if (!await operate(async (project, current) => {
      const result = await api('/api/script/editor/merge', {project_id: project, ...request});
      conflicts = result.conflicts;
      head = result.id;
      if (!conflicts.length && current.id !== head) {
        applyDraft(current, result);
        notice('已合并最新版本。可检查后保存全部。');
      } else if (!conflicts.length) notice('当前草稿已基于最新版本。');
    }) || !conflicts.length) return;
    const decisions = new Map();
    const fields = conflicts.map((conflict) => {
      const section = element('section', '', 'ide-merge-conflict');
      const decision = choices([['', '选择要保留的内容'], ['draft', '当前草稿'], ['latest', '最新保存版']]);
      decision.required = true;
      decisions.set(conflict.key, decision);
      const comparison = element('details');
      comparison.append(element('summary', '预览两份内容'));
      for (const [name, value] of [['当前草稿', conflict.draft], ['最新保存版', conflict.latest]]) {
        comparison.append(element('strong', name), element('pre', value === null ? '已删除'
          : value.length > 8000 ? value.slice(0, 8000) + '\n…' : value));
      }
      section.append(fieldLabel(conflict.file, decision), comparison);
      return section;
    });
    editForm('选择冲突文件的版本', fields, () => operate(async (project, current) => {
      const resolutions = Object.fromEntries([...decisions].map(([key, select]) => [key, select.value]));
      const result = await api('/api/script/editor/merge', {project_id: project, ...request, expected_head: head, resolutions});
      if (result.conflicts.length) throw new Error('仍有冲突，请重新合并。');
      applyDraft(current, result);
      notice('冲突已合并到草稿。可检查后保存全部。');
    }, get('edit-error')));
  }

  async function show(mode = view) {
    view = mode;
    const stamp = ++ticket;
    for (const control of nav) {
      const current = control.dataset.ideView === view;
      control.setAttribute('aria-pressed', String(current));
      get('view-' + control.dataset.ideView).hidden = !current;
    }
    sync();
    const buffer = active();
    if (!buffer?.loaded) return;
    try {
      if (mode === 'story' || mode === 'flow') await readStory(stamp, buffer);
      else if (mode === 'screens') await readScreens(stamp, buffer);
      else if (mode === 'history') await readHistory(stamp, buffer);
      else if (mode === 'release') await readVariants(stamp, buffer);
      else if (mode === 'localization') drawLocalization(buffer);
    } catch (error) {
      if (stamp !== ticket || buffer !== active()) return;
      if (mode === 'screens') {
        screens = {};
        screen = '';
        widget = -1;
        drawScreens();
        get('screen-status').textContent = error.message;
      } else notice(error.message, true);
    }
  }

  for (const control of nav) control.addEventListener('click', () => show(control.dataset.ideView));
  get('edit-cancel').onclick = () => edit.close();
  get('edit-form').onsubmit = async (event) => {
    event.preventDefault();
    const apply = event.currentTarget.querySelector('[type=submit]');
    const error = get('edit-error');
    apply.disabled = true;
    error.hidden = true;
    try {
      if (await editAction()) {
        edit.close();
        await show();
      }
    } catch (failure) {
      error.hidden = false;
      error.textContent = failure.message;
    } finally { apply.disabled = false; }
  };
  get('scene-add').onclick = () => addNamed('scene');
  get('character-add').onclick = () => addNamed('character');
  get('scene-source').onclick = () => jumpToSource(scene.file, scene.line);
  get('row-add').onclick = () => rowForm(null, 'insert');
  get('screen-source').onclick = () => jumpToSource(screenFile, 1);
  get('screen-add').onclick = () => {
    const name = input('', {required: true, maxLength: 64, pattern: '[A-Za-z_][A-Za-z_0-9]*'});
    editForm('新建界面', [fieldLabel('界面名称', name)], async () => {
      if (Object.hasOwn(screens, name.value)) throw new Error('已有同名界面。');
      const result = await writeScreens({...screens, [name.value]: []});
      if (result) { screen = name.value; widget = -1; }
      return result;
    });
  };
  get('variable-add').onclick = () => variableForm();
  get('widget-add').onclick = async () => {
    const items = screens[screen];
    if (await writeScreens({...screens, [screen]: [...items, {type: 'button', value: '继续', x: 20, y: 20,
      w: 35, h: 12, action: 'return', target: ''}]})) {
      widget = items.length;
      drawScreens();
      get('widget-value').focus();
    }
  };
  get('widget-type').onchange = updateWidgetFields;
  get('widget-action').onchange = updateWidgetFields;
  get('widget-form').onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(['type', 'value', 'action', 'target'].map((key) => [key, get('widget-' + key).value]));
    if (!get('widget-argument-row').hidden) data.argument = get('widget-argument').value;
    if (get('widget-visible_when').value) data.visible_when = get('widget-visible_when').value;
    for (const key of ['x', 'y', 'w', 'h']) data[key] = get('widget-' + key).valueAsNumber;
    const items = screens[screen].with(widget, data);
    if (await writeScreens({...screens, [screen]: items})) drawScreens();
  };
  get('widget-delete').onclick = async () => {
    const items = screens[screen].filter((_, index) => index !== widget);
    if (await writeScreens({...screens, [screen]: items})) { widget = -1; drawScreens(); }
  };
  get('history-refresh').onclick = () => show('history');
  get('localized-add').onclick = localizationForm;
  get('variant-add').onclick = () => variantForm().catch((error) => notice(error.message, true));
  get('history-more').onclick = () => readHistory(ticket, active(), true).catch((error) => notice(error.message, true));
  get('history-restore').onclick = async () => {
    if (active()?.dirty && !confirm('载入历史版本会替换当前未保存的草稿。继续？')) return;
    if (await operate(async (project, buffer) => {
      const {files, project_assets, managed_assets} = await api('/api/script/editor/open', {project_id: project, revision});
      applyDraft(buffer, {files, project_assets, managed_assets});
      notice('已作为草稿载入。保存后会创建新的工程版本。');
    })) show('source');
  };

  return {show, sync, merge, selectedScene: () => view === 'story' ? scene?.name : '',
    reset() {
      ++ticket;
      scene = undefined;
      screen = revision = nextRevision = '';
      widget = -1;
      screens = {};
      variables = {};
      history = [];
      variants = {};
      get('variant-list').replaceChildren();
      get('variant-status').textContent = '';
      if (edit.open) edit.close();
      show('source');
    }};
}
