(() => {
  const get = (id) => field(`ide-${id}`);
  const pane = field('script-ide');
  const code = get('code');
  const buffers = new Map();
  const empty = () => ({id: '', previous: '', text: '', managed_assets: {}, dirty: false, busy: false});
  const active = () => buffers.get(openProject?.id);
  const actionLabels = {path: '文件路径', background: '背景', portrait: '立绘', music: '音乐', sound: '音效', voice: '配音'};
  let lastReported = '';
  let sourceMode = false;
  const story = attachStoryEditor({requestJson,
    current: () => active() && {project: openProject.id, text: active().text, assets: active().managed_assets},
    assetName: (path) => {
      const id = active()?.managed_assets[path];
      if (!id) return '';
      return [...get('asset').options].find((option) => option.value === id)?.textContent
        || [...uploads].find(([, file]) => managedAssets.get(file) === id)?.[0];
    },
    openSource: (line) => {
      setMode(true);
      const lines = code.value.split('\n');
      const start = lines.slice(0, line - 1).join('\n').length + (line > 1 ? 1 : 0);
      reveal(start, start + (lines[line - 1]?.length || 0));
    },
    apply: async (changes) => {
      let result;
      const saved = await edit(async (project, buffer) => {
        result = await requestJson('/api/script/story/edit', {project_id: project, text: buffer.text, ...changes});
        return {text: result.text, managed_assets: {...buffer.managed_assets, ...result.managed_assets}, dirty: true};
      });
      if (saved && !sourceMode) story.refresh(result.scene, changes.operation === 'insert' ? undefined : result.line);
      return saved;
    },
  });

  function setMode(source) {
    sourceMode = source;
    get('source-editor').hidden = !source;
    get('story').hidden = source;
    get('source-mode').setAttribute('aria-pressed', String(source));
    get('story-mode').setAttribute('aria-pressed', String(!source));
    get('position').hidden = !source;
    get('insert').textContent = source ? '插入到光标' : '插入剧情行';
    updateAsset();
    if (!source) story.refresh();
  }

  function notice(message, error = false) {
    get('message').textContent = message;
    get('message').classList.toggle('error-text', error);
  }

  function position() {
    const before = code.value.slice(0, code.selectionStart);
    get('position').textContent = `行 ${before.split('\n').length}，列 ${before.length - before.lastIndexOf('\n')}`;
  }

  function outline() {
    const labels = code.value.matchAll(/^label\s+([A-Za-z_]\w*)\s*(?:\([^\n]*\))?\s*:/gm);
    get('label').replaceChildren(new Option('跳转到 label', ''),
      ...Array.from(labels, (label) => new Option(label[1], String(label.index))));
  }

  function controls(buffer = active()) {
    const busy = !buffer || buffer.busy;
    code.readOnly = busy;
    if (buffer?.busy) pane.dataset.busy = 'true';
    else delete pane.dataset.busy;
    get('save').disabled = busy || !buffer.dirty || !buffer.text.trim();
    get('generate').disabled = busy;
    get('import').disabled = busy;
    get('reload').disabled = busy;
    get('previous').disabled = busy || !buffer.previous;
    get('insert').disabled = busy || !get('asset').value || !get('action').value;
    get('export').disabled = busy;
    field('story-new-name').disabled = busy;
    get('story').inert = busy;
    get('version').textContent = buffer?.dirty ? '草稿未保存' : buffer?.id ? `已保存 ${buffer.id.slice(0, 10)}` : '';
    const revision = buffer && !buffer.dirty && buffer.id;
    const report = `${openProject?.id}/${revision}/${busy}`;
    if (report !== lastReported) {
      lastReported = report;
      reportNodeOutput('script', revision
        ? {reference: {kind: 'script', id: revision}, label: '已保存的 Ren’Py 草稿'} : null);
    }
  }

  function show(buffer) {
    const start = code.selectionStart;
    code.value = buffer?.text || '';
    code.setSelectionRange(start, start);
    outline();
    position();
    controls(buffer);
    if (!sourceMode && !buffer?.busy) story.refresh();
  }

  async function requestJson(path, data) {
    return (await request(path, data)).json();
  }

  async function edit(task) {
    const project = openProject?.id;
    const buffer = active();
    if (!buffer || buffer.busy) return false;
    buffer.busy = true;
    controls(buffer);
    notice('');
    try {
      const result = await task(project, buffer);
      if (result) Object.assign(buffer, result);
      if (project === openProject?.id) show(buffer);
      return project === openProject?.id;
    } catch (error) {
      if (project === openProject?.id) notice(error.message, true);
      return false;
    } finally {
      buffer.busy = false;
      if (project === openProject?.id) {
        controls(buffer);
        if (!sourceMode) story.refresh();
      }
    }
  }

  function loadLatest() {
    return edit(async (project) => ({...await requestJson('/api/script/editor/open', {project_id: project}), dirty: false}));
  }

  function save() {
    return edit(async (project, buffer) => ({...await requestJson('/api/script/editor/save', {
      project_id: project, base: buffer.id, text: buffer.text, managed_assets: buffer.managed_assets,
    }), dirty: false}));
  }

  function updateAsset() {
    const selected = get('asset').selectedOptions[0];
    const name = selected?.value && selected.textContent;
    const image = imageExtensions.test(name || '');
    const audio = audioExtensions.test(name || '');
    const actions = image ? ['background', 'portrait'] : audio ? ['music', 'sound', 'voice'] : [];
    if (sourceMode) actions.unshift('path');
    const previous = get('action').value;
    get('action').replaceChildren(...actions.map((action) => new Option(actionLabels[action], action)));
    if (!actions.length) get('action').append(new Option('在源码视图插入路径', ''));
    if (actions.includes(previous)) get('action').value = previous;
    get('media').replaceChildren();
    if (name && (image || audio)) {
      const media = document.createElement(image ? 'img' : 'audio');
      if (image) media.alt = name;
      else {
        media.controls = true;
        media.preload = 'metadata';
        media.setAttribute('aria-label', name);
      }
      media.src = `/api/workspace/assets/${encodeURIComponent(selected.value)}?preview=1`;
      get('media').append(media);
    }
    controls();
  }

  async function loadAssets(project) {
    get('assets-status').textContent = '正在读取项目资产…';
    try {
      const result = await requestJson('/api/pipeline/approved', {project_id: project});
      if (project !== openProject?.id) return;
      const assets = new Map(result.items.filter((entry) => !entry.needs_review)
        .flatMap((entry) => entry.references.filter((ref) => ref.kind === 'asset').map((ref) => [ref.id, ref.label])));
      const previous = get('asset').value;
      get('asset').replaceChildren(new Option('选择素材', ''),
        ...Array.from(assets, ([id, label]) => new Option(label, id)));
      if (assets.has(previous)) get('asset').value = previous;
      get('assets-status').textContent = assets.size ? '' : '暂无可插入资产。请先通过素材节点。';
      updateAsset();
    } catch (error) {
      if (project === openProject?.id) get('assets-status').textContent = error.message;
    }
  }

  code.addEventListener('input', () => {
    const buffer = active();
    if (!buffer) return;
    buffer.text = code.value;
    buffer.dirty = true;
    notice('');
    outline();
    position();
    controls(buffer);
  });
  for (const name of ['keyup', 'click', 'select']) code.addEventListener(name, position);
  pane.addEventListener('keydown', (event) => {
    if (!(event.ctrlKey || event.metaKey)) return;
    const key = event.key.toLowerCase();
    if (key === 's') {
      event.preventDefault();
      if (!get('save').disabled) save();
    } else if (key === 'f') {
      event.preventDefault();
      get('find').select();
      get('find').focus();
    }
  });
  code.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || code.readOnly || event.isComposing || event.ctrlKey || event.metaKey) return;
    const before = code.value.slice(0, code.selectionStart);
    const line = before.slice(before.lastIndexOf('\n') + 1);
    const indent = line.match(/^[\t ]*/)[0] + (line.trimEnd().endsWith(':') ? '    ' : '');
    if (!indent) return;
    event.preventDefault();
    code.setRangeText(`\n${indent}`, code.selectionStart, code.selectionEnd, 'end');
    code.dispatchEvent(new Event('input', {bubbles: true}));
  });
  window.addEventListener('beforeunload', (event) => {
    if ([...buffers.values()].some((buffer) => buffer.dirty)) event.preventDefault();
  });
  get('label').addEventListener('change', () => {
    if (!get('label').value) return;
    const offset = Number(get('label').value);
    const end = code.value.indexOf('\n', offset);
    reveal(offset, end < 0 ? code.value.length : end);
  });
  function reveal(start, end) {
    code.focus();
    code.setSelectionRange(start, end);
    const line = code.value.slice(0, start).split('\n').length;
    code.scrollTop = Math.max(0, (line - 3) * parseFloat(getComputedStyle(code).lineHeight));
    position();
  }
  get('find-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const query = get('find').value;
    if (!query) return;
    let offset = code.value.indexOf(query, code.selectionEnd);
    if (offset < 0) offset = code.value.indexOf(query);
    if (offset < 0) notice('没有找到匹配内容。');
    else reveal(offset, offset + query.length);
  });
  get('save').addEventListener('click', save);
  get('import').addEventListener('change', (event) => {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file || (active()?.text && !confirm('导入会替换编辑区，已保存的版本会保留。继续导入？'))) return;
    edit(async () => {
      if (file.size > 4_000_000) throw new Error('脚本文件最多 4 MB。');
      const text = (await file.text()).replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
      if (text.length > 1_000_000) throw new Error('脚本最多 100 万字符。');
      return {text, dirty: true};
    });
  });
  get('story-mode').addEventListener('click', () => setMode(false));
  get('source-mode').addEventListener('click', () => setMode(true));
  get('export').addEventListener('click', () => edit(async (project) => {
    const {inputs} = await requestJson('/api/pipeline/inputs', {project_id: project, node: 'export'});
    const script = inputs.flatMap((entry) => entry.references).find((ref) => ref.kind === 'script');
    if (!script) throw new Error('请先保存并通过要导出的脚本版本。');
    const response = await request('/api/build', {project_id: project, script_id: script.id});
    const blob = await response.blob();
    if (project !== openProject?.id) return;
    download(blob, 'prisma-renpy.zip');
    const id = response.headers.get('X-Prisma-Export-Id');
    if (id) reportNodeOutput('export', {reference: {kind: 'export', id}, label: 'Ren’Py 工程 ZIP'});
    notice(`已导出通过的版本 ${script.id.slice(0, 10)}。`);
  }));
  get('reload').addEventListener('click', () => {
    if (!active()?.dirty || confirm('放弃当前未保存的修改并读取最新版本？')) loadLatest();
    if (openProject) loadAssets(openProject.id);
  });
  get('previous').addEventListener('click', () => {
    if (active()?.dirty && !confirm('舍弃未保存的修改并载入上一版？')) return;
    edit(async (project, buffer) => {
      const previous = await requestJson('/api/script/editor/open', {project_id: project, revision: buffer.previous});
      return {text: previous.text, managed_assets: previous.managed_assets, dirty: true};
    });
  });
  get('generate').addEventListener('click', () => {
    if (active()?.text && !confirm('从 Scenario 重新生成会替换编辑区。已保存的脚本仍保留在版本历史。继续生成？')) return;
    edit(async () => {
      const input = scriptInputs();
      if (!input.scenario) throw new Error('请先生成或导入 Scenario。');
      const result = await requestJson('/api/script/preview', input);
      return {text: result.script, managed_assets: result.managed_assets, dirty: true};
    });
  });
  get('asset').addEventListener('change', updateAsset);
  get('insert').addEventListener('click', () => {
    const asset = get('asset').value;
    const action = get('action').value;
    if (!sourceMode) return story.insertAsset(asset, action);
    const start = code.selectionStart;
    const end = code.selectionEnd;
    edit(async (project, buffer) => {
      const result = await requestJson('/api/script/editor/asset', {project_id: project, asset_id: asset, action});
      const row = buffer.text.slice(0, start).lastIndexOf('\n') + 1;
      const indent = buffer.text.slice(row).match(/^[\t ]*/)[0];
      const snippet = action === 'path' ? result.snippet : `${indent || '    '}${result.snippet}\n`;
      const from = action === 'path' ? start : row;
      const to = action === 'path' ? end : row;
      const value = buffer.text.slice(0, from) + snippet + buffer.text.slice(to);
      if (project === openProject?.id) requestAnimationFrame(() => reveal(from, from + snippet.length));
      return {text: value, managed_assets: {...buffer.managed_assets, [result.path]: result.asset_id}, dirty: true};
    });
  });

  document.addEventListener('prisma-project-change', () => {
    get('asset').replaceChildren(new Option('选择素材', ''));
    get('media').replaceChildren();
    get('find').value = '';
    story.reset();
    setMode(false);
    notice('');
    show(active());
  });
  document.addEventListener('prisma-node-tools', ({detail}) => {
    if (detail !== 'script' || !openProject) return;
    const project = openProject.id;
    if (!buffers.has(project)) {
      buffers.set(project, empty());
      loadLatest();
    }
    show(active());
    loadAssets(project);
  });
  setMode(false);
})();
