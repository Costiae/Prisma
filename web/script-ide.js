(() => {
  const get = (name) => field(`ide-${name}`);
  const pane = field('script-ide');
  if (typeof PrismaEditorEngine === 'undefined' || typeof attachScriptWorkspace === 'undefined') {
    get('message').textContent = '当前 Prisma 服务未加载源码编辑器。使用更新后的服务打开此项目。';
    get('message').classList.add('error-text');
    get('message').hidden = false;
    pane.querySelectorAll('button, input, select').forEach((control) => { control.disabled = true; });
    return;
  }
  const buffers = new Map();
  const handoffs = new Map();
  const active = () => buffers.get(openProject?.id);
  const sourceType = /\.(rpy|rpym|py|json|txt|md|csv|toml|xml)$/i;
  const mediaType = /\.(png|jpe?g|webp|ogg|opus|mp3|wav|flac|ttf|otf|ttc)$/i;
  const actions = {path: '文件路径', background: '背景', portrait: '立绘', music: '音乐', sound: '音效', voice: '配音'};
  const preview = attachStoryPreview((text, scene) => api('/api/script/story', {text, scene}));
  let mounted;
  let mountedName = '';
  let lastReported = '';
  let approved = [];
  let assetRequest = 0;
  let isRename = false;
  let sdkAvailable = false;
  let polling;
  let workspace;
  let publishedId = '';

  const editor = PrismaEditorEngine.openEditor(get('code'), {
    onChange(text) {
      if (!mounted || !mountedName) return;
      mounted.files[mountedName] = text;
      mounted.dirty = true;
      if (mounted === active()) {
        sync();
        drawOutline();
      }
    },
    onPosition(line, column) { get('position').textContent = `行 ${line}，列 ${column}`; },
    onSave: () => { if (!get('save').disabled) save(); },
    onProjectSearch() { get('search-tools').open = true; get('search').focus(); },
    labels: () => Object.values(active()?.files || {}).flatMap((text) => symbols(text)
      .filter((symbol) => symbol.kind === 'label').map((symbol) => symbol.name)),
  });

  function empty() {
    return {id: '', previous: '', files: {}, managed_assets: {}, project_assets: {}, dirty: false, busy: false,
      name: '', tabs: [], states: new Map(), folders: new Map(), loaded: false};
  }

  async function api(path, data) { return (await request(path, data)).json(); }

  function element(tag, text = '', className = '') {
    const node = document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  }

  function button(text, click, label) {
    const control = element('button', text);
    control.type = 'button';
    if (label) control.setAttribute('aria-label', label);
    control.addEventListener('click', click);
    return control;
  }

  function notice(message, error = false) {
    get('message').textContent = message;
    get('message').hidden = !message;
    get('message').classList.toggle('error-text', error);
  }

  function symbols(text) {
    const out = [];
    text.split('\n').forEach((line, index) => {
      const match = /^(label|screen|transform|define|image|default)\s+([^:(=\s]+(?:\s+[^:(=\s]+)*?)\s*(?=[:(=])/.exec(line);
      if (match) out.push({kind: match[1], name: match[2], line: index + 1});
    });
    return out;
  }

  function drawOutline() {
    const buffer = active();
    get('label').replaceChildren(new Option('跳转到符号', ''),
      ...symbols(buffer?.files[buffer.name] || '').map((item) => new Option(`${item.kind} ${item.name}`, item.line)));
  }

  function sync(buffer = active()) {
    const busy = !buffer || buffer.busy;
    const hasFile = Boolean(buffer?.name);
    const anyFile = Boolean(buffer && Object.keys(buffer.files).length);
    if (buffer?.busy) pane.dataset.busy = 'true';
    else delete pane.dataset.busy;
    editor.readOnly(busy || !hasFile);
    for (const id of ['generate', 'import', 'import-folder', 'reload', 'new']) get(id).disabled = busy;
    get('starter').disabled = busy || anyFile;
    for (const control of get('empty-actions').querySelectorAll('[data-ide-empty]')) {
      control.disabled = busy || get(control.dataset.ideEmpty).disabled;
    }
    get('merge').disabled = busy || !anyFile || !buffer.dirty;
    get('approve').disabled = busy || !buffer.id || buffer.dirty || publishedId === buffer.id;
    get('export').disabled = busy || !publishedId;
    get('build-start').disabled = busy || !sdkAvailable || !publishedId || Boolean(buffer?.sdkJob);
    get('translation-generate').disabled = busy || !sdkAvailable || !buffer?.id || buffer.dirty || Boolean(buffer?.sdkJob);
    get('translation-import').hidden = !buffer?.translationJob;
    get('translation-import').disabled = busy || buffer?.dirty;
    for (const id of ['rename', 'delete', 'find', 'label']) get(id).disabled = busy || !hasFile;
    for (const id of ['play', 'check', 'search']) get(id).disabled = busy || !anyFile;
    get('previous').disabled = busy || !buffer.previous;
    get('insert').disabled = busy || !hasFile || !get('asset').value || !get('action').value;
    get('save').disabled = busy || !buffer.dirty || !anyFile;
    for (const mode of ['lint', 'run']) get(`sdk-${mode}`).disabled = busy || !sdkAvailable || Boolean(buffer?.sdkJob);
    get('sdk-stop').hidden = !buffer?.sdkJob;
    get('version').textContent = buffer?.dirty ? '有未保存的修改' : buffer?.id ? `工程版本 ${buffer.id.slice(0, 10)}` : '';
    const revision = buffer && !buffer.dirty && buffer.id;
    const report = `${openProject?.id}/${revision}/${busy}`;
    if (report !== lastReported) {
      lastReported = report;
      reportNodeOutput('script', revision ? {reference: {kind: 'script', id: revision}, label: '已保存的 Ren’Py 工程'} : null);
    }
    workspace?.sync();
  }

  function openFile(name, line, column, size) {
    const buffer = active();
    if (!buffer || buffer.busy || !Object.hasOwn(buffer.files, name)) return;
    buffer.name = name;
    if (!buffer.tabs.includes(name)) buffer.tabs.push(name);
    show();
    if (line) editor.reveal(line, column, size);
  }

  function selectedAsset() { return get('asset').selectedOptions[0]; }

  function drawTree(buffer) {
    const group = {folders: new Map(), files: []};
    for (const path of [...Object.keys(buffer.files), ...Object.keys(buffer.project_assets)].sort()) {
      const parts = path.split('/');
      const name = parts.pop();
      let cursor = group;
      for (const part of parts) {
        if (!cursor.folders.has(part)) cursor.folders.set(part, {folders: new Map(), files: []});
        cursor = cursor.folders.get(part);
      }
      cursor.files.push({path, name});
    }
    function draw(parent, node, prefix) {
      for (const [name, child] of node.folders) {
        const folderPath = prefix + name + '/';
        const details = element('details');
        details.open = buffer.folders.get(folderPath) ?? true;
        details.ontoggle = () => buffer.folders.set(folderPath, details.open);
        details.append(element('summary', name));
        draw(details, child, folderPath);
        parent.append(details);
      }
      for (const file of node.files) {
        const control = button(file.name, () => {
          if (Object.hasOwn(buffer.files, file.path)) openFile(file.path);
          else {
            get('asset').value = `file:${file.path}`;
            drawAsset();
          }
        }, `打开 ${file.path}`);
        control.title = file.path;
        if (file.path === buffer.name) control.setAttribute('aria-current', 'true');
        parent.append(control);
      }
    }
    get('tree').replaceChildren();
    draw(get('tree'), group, '');
  }

  function closeTab(buffer, name) {
    buffer.tabs = buffer.tabs.filter((path) => path !== name);
    if (buffer.name === name) buffer.name = buffer.tabs.at(-1) || '';
    show();
  }

  function show(buffer = active()) {
    const file = buffer?.name || '';
    if (mounted !== buffer || mountedName !== file || (file && editor.state().doc.toString() !== buffer.files[file])) {
      if (mountedName && mounted?.files[mountedName] === editor.state().doc.toString()) {
        mounted.states.set(mountedName, editor.state());
      }
      mounted = buffer;
      mountedName = file;
      const cached = buffer?.states.get(file);
      const source = file ? buffer.files[file] : '';
      editor.show(cached && cached.doc.toString() === source ? cached : editor.create(file, source));
      const state = editor.state();
      const head = state.selection.main.head;
      const line = state.doc.lineAt(head);
      get('position').textContent = file ? `行 ${line.number}，列 ${head - line.from + 1}` : '';
    }
    get('code').hidden = !file;
    get('file-tools').hidden = !file;
    get('empty').hidden = Boolean(file);
    const existing = Boolean(buffer && Object.keys(buffer.files).length);
    get('empty-title').textContent = existing ? '打开工程文件' : '开始制作';
    get('empty-detail').textContent = existing ? '从左侧工程树选择文件。' : '从项目剧本生成起稿，或导入已有的 Ren’Py 工程。';
    get('empty-actions').hidden = existing;
    get('current-file').textContent = file ? `game/${file}` : '未打开文件';
    const tabs = (buffer?.tabs || []).map((name) => {
      const item = element('div', '', 'ide-tab');
      const tab = button(name.split('/').pop(), () => openFile(name), `切换到 ${name}`);
      tab.title = name;
      tab.setAttribute('aria-pressed', String(file === name));
      const close = button('', () => closeTab(buffer, name), `关闭标签页 ${name}`);
      close.className = 'ide-tab-close';
      close.append(uiIcon('close'));
      item.append(tab, close);
      return item;
    });
    get('tabs').replaceChildren(...tabs);
    if (buffer) drawTree(buffer);
    else get('tree').replaceChildren();
    drawOutline();
    sync(buffer);
  }

  function adopt(buffer, revision, dirty, select) {
    const oldName = buffer.name;
    Object.assign(buffer, revision, {dirty});
    buffer.tabs = buffer.tabs.filter((name) => Object.hasOwn(buffer.files, name));
    buffer.name = [select, oldName, 'script.rpy', ...Object.keys(buffer.files)]
      .find((name) => name && Object.hasOwn(buffer.files, name)) || '';
    if (buffer.name && !buffer.tabs.includes(buffer.name)) buffer.tabs.push(buffer.name);
  }

  async function operate(task, errorSlot) {
    const project = openProject?.id;
    const buffer = active();
    if (!buffer || buffer.busy) return;
    buffer.busy = true;
    sync();
    notice('');
    try {
      await task(project, buffer);
      return project === openProject?.id;
    } catch (error) {
      if (project === openProject?.id) {
        if (errorSlot) {
          errorSlot.textContent = error.message;
          errorSlot.hidden = false;
        } else notice(error.message, true);
      }
      return false;
    } finally {
      buffer.busy = false;
      if (project === openProject?.id) {
        show(buffer);
        takeHandoff(project, buffer);
      }
    }
  }

  function payload(project, buffer) {
    return {project_id: project, files: buffer.files, managed_assets: buffer.managed_assets, project_assets: buffer.project_assets};
  }

  function loadLatest() {
    return operate(async (project, buffer) => {
      const response = await api('/api/script/editor/open', {project_id: project});
      adopt(buffer, response, false);
      buffer.loaded = true;
      if (project === openProject?.id) {
        drawAssets();
        workspace.show();
      }
    });
  }

  function takeHandoff(project, buffer) {
    const handoff = handoffs.get(project);
    if (!handoff || buffer.busy || !buffer.loaded || !['#stage-script', '#script-ide-workspace'].includes(location.hash)) return;
    handoffs.delete(project);
    const scripts = Object.keys(buffer.files).filter((name) => /\.rpy$/i.test(name));
    if (!scripts.length) {
      adopt(buffer, {files: {...buffer.files, 'script.rpy': handoff.text},
        managed_assets: {...buffer.managed_assets, ...handoff.assets}}, true, 'script.rpy');
      show(buffer);
      if (handoff.line) editor.reveal(handoff.line);
      notice('已把演出预览载入当前项目的工程草稿。');
      return;
    }
    const target = scripts.includes('script.rpy') ? 'script.rpy' : scripts[0];
    const source = buffer.files[target];
    let line = source === handoff.text ? handoff.line : undefined;
    if (!line && handoff.anchor) {
      const index = source.split('\n').findIndex((row) => row.trim() === handoff.anchor || row.trim().startsWith(`${handoff.anchor} `));
      if (index >= 0) line = index + 1;
    }
    openFile(target, line);
    if (source !== handoff.text) notice('已打开当前工程。可用「从 Scenario 生成」更新 script.rpy。');
  }

  function save() {
    return operate(async (project, buffer) => {
      let response;
      try {
        response = await api('/api/script/editor/save', {...payload(project, buffer), base: buffer.id});
      } catch (error) {
        if (error.message.includes('脚本已有较新版本')) throw new Error('脚本已有较新版本。可从「工程 → 合并最新版本」继续。');
        throw error;
      }
      adopt(buffer, response, false);
      if (project === openProject?.id) {
        notice(`已保存 ${Object.keys(buffer.files).length} 个源文件。`);
        workspace.show();
      }
    });
  }

  function drawAssets() {
    const select = get('asset');
    const before = select.value;
    const options = [new Option('选择素材', '')];
    for (const entry of approved) {
      const option = new Option(entry.label, `asset:${entry.id}`);
      option.dataset.id = entry.id;
      options.push(option);
    }
    for (const [path, id] of Object.entries(active()?.project_assets || {}).sort()) {
      const option = new Option(path, `file:${path}`);
      option.dataset.id = id;
      option.dataset.path = path;
      options.push(option);
    }
    select.replaceChildren(...options);
    select.value = options.some((option) => option.value === before) ? before : '';
    drawAsset();
    get('assets-status').textContent = options.length > 1 ? '' : '暂无素材。可导入工程文件或通过项目素材节点。';
  }

  function drawAsset() {
    const item = selectedAsset();
    const name = item?.textContent || '';
    const image = imageExtensions.test(name);
    const audio = audioExtensions.test(name);
    const choices = ['path'];
    if (!item?.dataset.path && image) choices.push('background', 'portrait');
    if (!item?.dataset.path && audio) choices.push('music', 'sound', 'voice');
    const previous = get('action').value;
    get('action').replaceChildren(...choices.map((action) => new Option(actions[action], action)));
    if (choices.includes(previous)) get('action').value = previous;
    get('media').replaceChildren();
    if (item?.dataset.id && (image || audio)) {
      const media = document.createElement(image ? 'img' : 'audio');
      if (image) media.alt = name;
      else {
        media.controls = true;
        media.preload = 'metadata';
        media.setAttribute('aria-label', name);
      }
      media.src = `/api/workspace/assets/${encodeURIComponent(item.dataset.id)}?preview=1`;
      get('media').append(media);
    }
    sync();
  }

  async function refreshAssets(project) {
    const token = ++assetRequest;
    try {
      const result = await api('/api/pipeline/approved', {project_id: project});
      if (token !== assetRequest || project !== openProject?.id) return;
      approved = [...new Map(result.items.filter((entry) => !entry.needs_review).flatMap((entry) => entry.references)
        .filter((ref) => ref.kind === 'asset').map((ref) => [ref.id, ref])).values()];
      drawAssets();
    } catch (error) {
      if (token === assetRequest && project === openProject?.id) get('assets-status').textContent = error.message;
    }
  }

  function validateName(name, paths, old) {
    if (name.length > 240 || name.split('/').some((part) => !part || part === '.' || part === '..' || part.trim() !== part
      || part.endsWith('.') || /[\\:<>"|?*\u0000-\u001f]/.test(part) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part))) {
      throw new Error('请输入 game/ 下的可移植相对路径。');
    }
    const key = name.toLowerCase();
    if (paths.some((path) => {
      if (path === old) return false;
      const existing = path.toLowerCase();
      return existing === key || existing.startsWith(`${key}/`) || key.startsWith(`${existing}/`);
    })) throw new Error('工程中已有同名文件或目录。');
  }

  function fileDialog(rename) {
    isRename = rename;
    get('file-title').textContent = rename ? '移动 / 重命名文件' : '新建文件';
    get('file-path').value = rename ? active().name : '';
    get('file-error').hidden = true;
    get('file-dialog').showModal();
    get('file-path').select();
  }

  function importFiles(event, directory = false) {
    const items = Array.from(event.target.files);
    event.target.value = '';
    if (!items.length) return;
    const hasGame = directory && items.some((file) => file.webkitRelativePath.split('/')[1]?.toLowerCase() === 'game');
    const files = items.flatMap((file) => {
      const parts = file.webkitRelativePath.split('/');
      if (hasGame && parts[1]?.toLowerCase() !== 'game') return [];
      const name = directory ? parts.slice(hasGame ? 2 : 1).join('/') : file.name;
      return sourceType.test(name) || mediaType.test(name) ? [{name, file}] : [];
    });
    if (!files.length) return notice('目录中没有支持的 Ren’Py 源码、文本或媒体文件。', true);
    const buffer = active();
    if (!buffer) return;
    const paths = [...Object.keys(buffer.files), ...Object.keys(buffer.project_assets)];
    const overlap = files.filter(({name}) => paths.includes(name));
    if (overlap.length && !confirm(`覆盖当前草稿中的 ${overlap.length} 个同名文件？已保存的版本会保留。`)) return;
    operate(async (project, draft) => {
      const incoming = new Set();
      for (const {name, file} of files) {
        validateName(name, [...paths.filter((path) => path !== name), ...incoming]);
        incoming.add(name);
        if (sourceType.test(name) && file.size > 4_000_000) throw new Error(`文本文件过大「${name}」。`);
      }
      const sources = {...draft.files};
      const assets = {...draft.project_assets};
      let first;
      for (const {name, file} of files) {
        if (sourceType.test(name)) {
          const text = (await file.text()).replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
          if (text.length > 1_000_000 || text.includes('\0')) throw new Error(`文件不是受支持的文本「${name}」。`);
          sources[name] = text;
          first ||= name;
        } else {
          const asset = await registerMedia(name.split('/').pop(), {data: await encode(file)}, {project_id: project});
          assets[name] = asset.id;
        }
      }
      adopt(draft, {files: sources, project_assets: assets}, true, first);
      if (project === openProject?.id) {
        drawAssets();
        notice(`已导入 ${files.length} 个文件${items.length > files.length ? `，跳过 ${items.length - files.length} 个目录外或不支持的文件` : ''}。`);
      }
    });
  }

  get('new').onclick = () => fileDialog(false);
  get('rename').onclick = () => fileDialog(true);
  get('file-cancel').onclick = () => get('file-dialog').close();
  get('file-form').onsubmit = (event) => {
    event.preventDefault();
    const buffer = active();
    if (!buffer || buffer.busy) return;
    const old = isRename ? buffer.name : '';
    const name = get('file-path').value.trim();
    try {
      if (!sourceType.test(name)) throw new Error('请使用 .rpy、.py、.json 等源码或文本扩展名。');
      validateName(name, [...Object.keys(buffer.files), ...Object.keys(buffer.project_assets)], old);
      if (old !== name) {
        const files = {...buffer.files, [name]: old ? buffer.files[old] : ''};
        if (old) delete files[old];
        adopt(buffer, {files}, true, name);
      }
      get('file-dialog').close();
      show(buffer);
      editor.reveal(1);
    } catch (error) {
      get('file-error').textContent = error.message;
      get('file-error').hidden = false;
    }
  };
  get('delete').onclick = () => {
    const buffer = active();
    const name = buffer.name;
    if (!confirm(`从草稿中删除「${name}」？`)) return;
    delete buffer.files[name];
    buffer.states.delete(name);
    adopt(buffer, {}, true);
    show(buffer);
  };
  get('find').onclick = () => editor.find();
  get('label').onchange = () => { if (get('label').value) editor.reveal(Number(get('label').value)); };
  get('search-form').onsubmit = (event) => {
    event.preventDefault();
    const query = get('search').value.trim();
    if (!query) return;
    const pattern = new RegExp(query.replace(/[.*+?^\${}()|[\]\\]/g, '\\$&'), 'i');
    const matches = [];
    for (const [name, text] of Object.entries(active().files)) {
      text.split('\n').forEach((line, index) => {
        const match = pattern.exec(line);
        if (match && matches.length < 201) matches.push({name, line: index + 1, column: match.index + 1, text: line.trim()});
      });
    }
    get('search-status').textContent = matches.length > 200 ? '显示前 200 行匹配' : `${matches.length} 行匹配`;
    get('results').replaceChildren(...matches.slice(0, 200).map((match) => {
      const item = element('li');
      const title = `${match.name}:${match.line}`;
      item.append(button(`${title} · ${match.text.slice(0, 88)}`,
        () => openFile(match.name, match.line, match.column, query.length)));
      return item;
    }));
  };
  get('import').onchange = (event) => importFiles(event);
  get('import-folder').onchange = (event) => importFiles(event, true);
  get('save').onclick = save;
  get('reload').onclick = () => {
    if (!active()?.dirty || confirm('放弃当前未保存的修改并读取最新版本？')) loadLatest();
    if (openProject) refreshAssets(openProject.id);
  };
  get('merge').onclick = () => workspace.merge();
  get('previous').onclick = () => {
    if (active()?.dirty && !confirm('舍弃未保存的修改并载入上一版？')) return;
    operate(async (project, buffer) => {
      const {files, managed_assets, project_assets} = await api('/api/script/editor/open', {project_id: project, revision: buffer.previous});
      adopt(buffer, {files, managed_assets, project_assets}, true);
      if (project === openProject?.id) drawAssets();
    });
  };
  get('generate').onclick = () => {
    if (Object.hasOwn(active().files, 'script.rpy') && !confirm('重新生成会替换草稿中的 script.rpy。继续？')) return;
    operate(async (_, buffer) => {
      const source = scriptInputs();
      if (!source.scenario) throw new Error('请先生成或导入 Scenario。');
      const result = await api('/api/script/preview', source);
      adopt(buffer, {files: {...buffer.files, 'script.rpy': result.script},
        managed_assets: {...buffer.managed_assets, ...result.managed_assets}}, true, 'script.rpy');
      if (buffer === active()) workspace.show('source');
    });
  };
  get('starter').onclick = () => operate(async (_, buffer) => {
    if (Object.keys(buffer.files).length || Object.keys(buffer.project_assets).length) {
      throw new Error('已有工程内容，请从文件树继续编辑。');
    }
    const result = await api('/api/script/editor/starter', {});
    adopt(buffer, result, true, 'script.rpy');
    if (buffer === active()) {
      workspace.show('source');
      notice('已创建入口、初始剧情和主菜单。保存后可进入发布。');
    }
  });
  get('asset').onchange = drawAsset;
  get('insert').onclick = () => operate(async (project, buffer) => {
    const selected = selectedAsset();
    const action = get('action').value;
    const registered = selected.dataset.path;
    const result = registered ? {snippet: JSON.stringify(registered)}
      : await api('/api/script/editor/asset', {project_id: project, asset_id: selected.dataset.id, action});
    if (result.path) buffer.managed_assets[result.path] = result.asset_id;
    if (project !== openProject?.id) return;
    let snippet = result.snippet;
    const text = Object.values(buffer.files).join('\n');
    if (action === 'background' && !/^transform prisma_backdrop\s*:/m.test(text)) snippet = snippet.replace(' at prisma_backdrop', '');
    if (action === 'portrait' && !/^transform prisma_portrait\s*:/m.test(text)) snippet = snippet.replace(' at prisma_portrait', '');
    editor.insert(snippet, action === 'path');
  });
  get('check').onclick = () => operate(async (project, buffer) => {
    const result = await api('/api/script/editor/check', payload(project, buffer));
    if (project !== openProject?.id) return;
    get('check-status').textContent = `${result.files} 个文件 · ${result.assets} 项素材 · ${result.issues.length} 项提示`;
    get('issues').replaceChildren(...result.issues.map((issue) => {
      const row = element('li');
      row.append(issue.file ? button(issue.message, () => {
        workspace.show('source');
        openFile(issue.file, issue.line);
      }) : element('p', issue.message));
      return row;
    }));
  });
  get('play').onclick = () => {
    const buffer = active();
    const files = Object.entries(buffer.files).filter(([name]) => /\.rpy$/i.test(name)).sort();
    const script = files.map(([, text]) => text).join('\n\n');
    preview.play(script, {...buffer.managed_assets, ...buffer.project_assets}, workspace.selectedScene() || 'start');
  };
  async function published(project, required = true) {
    const {inputs} = await api('/api/pipeline/inputs', {project_id: project, node: 'export'});
    const revision = inputs.flatMap((entry) => entry.references).find((ref) => ref.kind === 'script');
    if (!revision && required) throw new Error('请先保存并通过要使用的工程版本。');
    return revision;
  }

  async function refreshRelease(project) {
    try {
      const current = await published(project, false);
      if (project !== openProject?.id) return;
      publishedId = current?.id || '';
      get('release-status').textContent = current ? `已通过工程 ${publishedId.slice(0, 10)}` : '保存工程后通过当前版本，即可交给导出节点。';
      const variants = get('build-variant');
      const previous = variants.value;
      const data = current ? await api('/api/script/editor/variants', {project_id: project, revision: current.id}) : {variants: {}};
      if (project !== openProject?.id || publishedId !== (current?.id || '')) return;
      variants.replaceChildren(new Option('完整版本', 'main'), ...Object.keys(data.variants).map((id) => new Option(id, id)));
      if (Object.hasOwn(data.variants, previous)) variants.value = previous;
      sync();
    } catch (error) {
      if (project === openProject?.id) get('release-status').textContent = error.message;
    }
  }

  get('approve').onclick = () => operate(async (project, buffer) => {
    const {nodes, roadmap} = await api('/api/pipeline/state', {project_id: project});
    const node = nodes.script;
    const inputs = Object.fromEntries(roadmap.edges.filter(({target, source}) =>
      target === 'script' && nodes[source]?.status === 'released' && nodes[source].targets.includes('script'))
      .map(({source}) => [source, nodes[source].id]));
    await api('/api/pipeline/action', {project_id: project, node: 'script', base: node?.id || '',
      decision: 'pass', inputs, references: [{kind: 'script', id: buffer.id}]});
    if (buffer === active()) {
      await refreshRelease(project);
      document.dispatchEvent(new Event('prisma-pipeline-refresh'));
      notice('已通过工程版本并传给导出节点。');
    }
  });

  async function refreshSDK(project) {
    try {
      const {available, packages, updates} = await api('/api/script/sdk/status', {project_id: project});
      if (project !== openProject?.id) return;
      sdkAvailable = available;
      const targets = {win: 'Windows', linux: 'Linux', mac: 'macOS', web: 'Web'};
      const target = get('build-target');
      const before = target.value;
      target.replaceChildren(...packages.map((id) => new Option(targets[id], id)));
      if (packages.includes(before)) target.value = before;
      get('update-option').hidden = !updates;
      get('build-updates').disabled = target.value === 'web';
      if (!active()?.sdkJob) get('sdk-status').textContent = available ? '使用已通过的工程版本。' : '设置 PRISMA_RENPY_SDK 后可使用本机引擎。';
      if (!active()?.translationJob && active()?.sdkMode !== 'translate') {
        get('translation-status').textContent = available ? '保存工程后，从当前源码生成翻译模板。' : '配置本机 Ren’Py SDK 后可生成翻译模板。';
      }
      sync();
    } catch (error) {
      if (project === openProject?.id) get('sdk-status').textContent = error.message;
    }
  }

  async function pollSDK(project, ident) {
    const buffer = buffers.get(project);
    if (project !== openProject?.id || buffer?.sdkJob !== ident) return;
    clearTimeout(polling);
    try {
      const response = await fetch(`/api/images/jobs/${encodeURIComponent(ident)}`);
      const job = await response.json();
      if (project !== openProject?.id || buffer.sdkJob !== ident) return;
      if (!response.ok) throw new Error(job.error);
      get('sdk-status').textContent = job.error || job.phase;
      get('sdk-log').textContent = job.log || '';
      get('sdk-log').hidden = !job.log;
      if (job.kind === 'renpy_translate') get('translation-status').textContent = job.error || job.phase;
      if (['running', 'cancelling'].includes(job.state)) {
        polling = setTimeout(() => pollSDK(project, ident), 1100);
      } else {
        buffer.sdkJob = null;
        if (job.state === 'done' && job.kind === 'renpy_translate') {
          buffer.translationJob = ident;
          get('translation-status').textContent = '已生成翻译模板，可以载入草稿。';
        }
        if (job.state === 'done' && job.kind === 'renpy_build') {
          refreshDistributions(project);
        }
        buffer.sdkMode = '';
        sync();
      }
    } catch (error) {
      if (project === openProject?.id) get('sdk-status').textContent = error.message;
      if (buffer.sdkJob === ident) polling = setTimeout(() => pollSDK(project, ident), 2500);
    }
  }

  function drawDistributions() {
    get('build-files').replaceChildren(...(active()?.distribution || []).map((entry) => {
      const item = element('li');
      const link = element('a', entry.name);
      link.href = entry.url;
      link.download = '';
      const edition = entry.variant && entry.variant !== 'main' ? entry.variant : '完整版本';
      item.append(link, element('span', ' · ' + edition + ' · ' + Math.ceil(entry.size / (1024 * 1024)) + ' MB', 'hint'));
      return item;
    }));
  }

  async function refreshDistributions(project) {
    const buffer = active();
    if (!buffer) return;
    try {
      const {items} = await api('/api/script/sdk/builds', {project_id: project});
      if (buffer !== active()) return;
      buffer.distribution = items.filter((job) => job.state === 'done').flatMap((job) => job.packages.map((entry, index) => ({
        ...entry, variant: job.options.variant,
        url: '/api/script/sdk/files/' + encodeURIComponent(job.id) + '/' + index + '?project_id=' + encodeURIComponent(project),
      })));
      drawDistributions();
      const running = items.find((job) => ['running', 'cancelling'].includes(job.state));
      if (running && !buffer.sdkJob) {
        buffer.sdkJob = running.id;
        buffer.sdkMode = 'build';
        sync();
        pollSDK(project, running.id);
      }
    } catch (error) {
      if (buffer === active()) get('build-files').replaceChildren(element('li', error.message, 'error-text'));
    }
  }

  function startSDK(mode, options = {}) {
    return operate(async (project, buffer) => {
      const revision = mode === 'translate' ? buffer.id : (await published(project)).id;
      const job = await api('/api/script/sdk/start', {project_id: project, revision, mode, ...options});
      buffer.sdkJob = job.id;
      buffer.sdkMode = mode;
      if (project === openProject?.id) {
        get('sdk-status').textContent = `工程版本 ${revision.slice(0, 10)} · 正在启动…`;
        if (mode === 'translate') get('translation-status').textContent = '正在生成翻译模板…';
        pollSDK(project, job.id);
      }
    });
  }
  for (const mode of ['lint', 'run']) get(`sdk-${mode}`).onclick = () => startSDK(mode);
  get('build-form').onsubmit = (event) => {
    event.preventDefault();
    const updates = get('build-updates');
    startSDK('build', {package: get('build-target').value, variant: get('build-variant').value,
      updates: !updates.disabled && !get('update-option').hidden && updates.checked});
  };
  get('build-target').onchange = () => { get('build-updates').disabled = get('build-target').value === 'web'; };
  get('translation-form').onsubmit = (event) => {
    event.preventDefault();
    startSDK('translate', {language: get('language').value});
  };
  get('translation-import').onclick = () => operate(async (project, buffer) => {
    const result = await api('/api/script/sdk/translations', {project_id: project, job_id: buffer.translationJob});
    if (buffer.dirty || buffer.id !== result.revision) throw new Error('工程已变更。请从最新保存版本重新生成模板。');
    const files = Object.keys(result.files);
    if (!files.length) throw new Error('SDK 未生成可导入的翻译文件。');
    adopt(buffer, {files: {...buffer.files, ...result.files}}, true, files[0]);
    buffer.translationJob = '';
    if (buffer === active()) {
      workspace.show('source');
      notice(`已将 ${files.length} 个翻译模板载入草稿。`);
    }
  });
  get('sdk-stop').onclick = () => operate(async (_, buffer) => api('/api/images/cancel', {id: buffer.sdkJob}));
  get('export').onclick = () => operate(async (project) => {
    const revision = await published(project);
    const response = await request('/api/build', {project_id: project, script_id: revision.id});
    const blob = await response.blob();
    if (project !== openProject?.id) return;
    download(blob, 'prisma-renpy.zip');
    const id = response.headers.get('X-Prisma-Export-Id');
    if (id) reportNodeOutput('export', {reference: {kind: 'export', id}, label: 'Ren’Py 工程 ZIP'});
    notice(`已导出通过的版本 ${revision.id.slice(0, 10)}。`);
  });

  workspace = attachScriptWorkspace({get, pane, active, api, element, button, operate, openFile, notice,
    applyDraft(buffer, changes, select) {
      adopt(buffer, changes, true, select);
      if (buffer === active()) drawAssets();
    }});
  for (const control of pane.querySelectorAll('#ide-file-menu button, #ide-file-menu input')) {
    control.addEventListener('click', () => { get('file-menu').open = false; });
  }
  for (const control of pane.querySelectorAll('[data-ide-empty]')) {
    control.addEventListener('click', () => get(control.dataset.ideEmpty).click());
  }
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's'
        && !pane.closest('[hidden]') && !event.defaultPrevented) {
      event.preventDefault();
      if (!get('save').disabled) save();
    }
  });
  pane.querySelector('[data-ide-view="release"]').addEventListener('click', () => {
    if (!openProject) return;
    refreshRelease(openProject.id);
    refreshDistributions(openProject.id);
  });
  window.addEventListener('beforeunload', (event) => {
    if ([...buffers.values()].some((buffer) => buffer.dirty)) event.preventDefault();
  });
  document.addEventListener('prisma-project-change', () => {
    ++assetRequest;
    clearTimeout(polling);
    sdkAvailable = false;
    publishedId = '';
    approved = [];
    preview.close();
    if (get('file-dialog').open) get('file-dialog').close();
    for (const id of ['search-status', 'check-status', 'results', 'issues', 'sdk-status', 'sdk-log', 'release-status',
      'translation-status', 'languages', 'build-files']) get(id).replaceChildren();
    get('build-variant').replaceChildren(new Option('完整版本', 'main'));
    get('update-option').hidden = true;
    get('build-updates').checked = false;
    get('sdk-log').hidden = true;
    get('search').value = '';
    notice('');
    workspace.reset();
    show();
    drawAssets();
  });
  document.addEventListener('prisma-script-handoff', ({detail}) => {
    handoffs.set(detail.project, detail);
  });
  function activate() {
    if (!openProject) return;
    const project = openProject.id;
    if (!buffers.has(project)) {
      buffers.set(project, empty());
      loadLatest();
    }
    show();
    takeHandoff(project, active());
    drawDistributions();
    refreshAssets(project);
    refreshRelease(project);
    refreshSDK(project);
    if (active().sdkJob) pollSDK(project, active().sdkJob);
  }
  document.addEventListener('prisma-node-tools', ({detail}) => {
    if (detail === 'script') activate();
  });
  document.addEventListener('prisma-view-change', ({detail}) => {
    if (detail === 'script-ide-workspace') activate();
  });
})();
