(() => {
  const get = (key) => field(`script-${key}`);
  const value = (key) => get(key).value;
  const actions = {
    background: '切换背景', show: '显示立绘', hide: '隐藏立绘', music: '播放音乐', stop: '停止音乐',
    sound: '播放音效', voice: '插入配音',
  };
  let preview = null;
  let point = null;
  let renderedInput = '';
  let running = false;

  function text(tag, content, className) {
    const element = document.createElement(tag);
    element.textContent = content;
    if (className) element.className = className;
    return element;
  }

  const input = scriptInputs;

  function status(message, error = false) {
    get('status').textContent = message;
    get('status').classList.toggle('error-text', error);
  }

  function updateMedia() {
    const name = value('asset');
    const file = uploads.get(name);
    const id = file && managedAssets.get(file);
    const container = get('media');
    container.replaceChildren();
    if (!id || get('asset-row').hidden) return;
    const element = imageExtensions.test(name) ? document.createElement('img') : document.createElement('audio');
    if (element.tagName === 'IMG') {
      element.alt = name;
    } else {
      element.controls = true;
      element.preload = 'metadata';
      element.setAttribute('aria-label', name);
    }
    element.src = `/api/workspace/assets/${encodeURIComponent(id)}?preview=1`;
    container.append(element);
  }

  function updateForm(preferred = value('asset')) {
    const kind = value('action');
    const image = ['background', 'show'].includes(kind);
    const hasAsset = !['hide', 'stop'].includes(kind);
    get('actor-row').hidden = !['show', 'hide'].includes(kind);
    get('asset-row').hidden = !hasAsset;
    const names = [...uploads.keys()].filter((name) => (image ? imageExtensions : audioExtensions).test(name));
    get('asset').replaceChildren(new Option('选择素材', ''), ...names.map((name) => new Option(name, name)));
    if (names.includes(preferred)) get('asset').value = preferred;
    const current = renderedInput === JSON.stringify(input());
    get('insert').disabled = running || !point || !current;
    get('preview').disabled = running;
    if (running) get('preview').dataset.busy = 'true';
    else delete get('preview').dataset.busy;
    updateMedia();
  }

  function describe(cue) {
    const [kind, value] = Object.entries(cue)[0];
    if (kind === 'music' && value === null) return actions.stop;
    return `${actions[kind]} · ${kind === 'show' ? `${value.actor} / ${value.file}` : value}`;
  }

  function selectPoint(index, scroll = false) {
    point = preview?.points[index] || null;
    get('point').value = point ? String(index) : '';
    get('location').textContent = point
      ? `${point.scene}/${point.index}　${point.speaker ? `${point.speaker}：` : ''}${point.text}`
      : '尚未选择台词';
    get('replace').replaceChildren(new Option('新增动作', ''),
      ...(point?.cues || []).map((cue, position) => new Option(describe(cue), String(position))));
    get('actor').value = point?.speaker || '';
    const code = get('code');
    code.querySelector('[aria-current]')?.removeAttribute('aria-current');
    const marker = point && code.querySelector(`[data-point="${index}"]`);
    if (marker) {
      marker.setAttribute('aria-current', 'true');
      if (scroll) marker.scrollIntoView({block: 'nearest'});
    }
    updateForm();
  }

  function render(result, selected = 0) {
    preview = result;
    renderedInput = JSON.stringify(input());
    const positions = new Map(result.points.map((point, index) => [point.script_line, index]));
    const content = document.createDocumentFragment();
    result.script.split('\n').forEach((line, offset) => {
      const scriptLine = offset + 1;
      let row;
      if (positions.has(scriptLine)) {
        const position = positions.get(scriptLine);
        const anchor = result.points[position];
        row = text('button', '', 'script-line script-marker');
        row.type = 'button';
        row.dataset.point = position;
        row.setAttribute('aria-label', `选择 ${anchor.scene}/${anchor.index}`);
        row.onclick = () => selectPoint(position);
      } else {
        row = text('div', '', 'script-line');
      }
      row.append(text('span', scriptLine, 'script-number'), text('code', line || ' '));
      content.append(row);
    });
    get('code').replaceChildren(content);
    get('point').replaceChildren(...result.points.map((point, index) =>
      new Option(`${point.scene}/${point.index} · ${point.text.slice(0, 36)}`, index)));
    selectPoint(selected, true);
    status(`${result.points.length} 条文本 · ${result.points.filter((point) => point.cues.length).length} 条有演出 · ${Object.keys(result.used_assets).length} 个已引用素材`);
  }

  async function refresh(lineId) {
    if (!scenarioData()) return status('请先生成或导入 Scenario。', true);
    if (running) return;
    if (lineId && renderedInput === JSON.stringify(input())) {
      const index = preview?.points.findIndex((item) => item.id === lineId);
      if (index >= 0) return selectPoint(index, true);
    }
    running = true;
    status('正在生成脚本预览…');
    updateForm();
    const submitted = input();
    try {
      const response = await request('/api/script/preview', submitted);
      const result = await response.json();
      if (JSON.stringify(submitted) === JSON.stringify(input())) {
        const selected = result.points.findIndex((item) => item.id === lineId);
        render(result, Math.max(0, selected));
      }
    } catch (error) { status(error.message, true); }
    finally { running = false; updateForm(); }
  }

  get('preview').onclick = () => refresh(point?.id);
  get('open-ide').onclick = () => {
    if (!openProject) return;
    if (preview && renderedInput === JSON.stringify(input())) {
      document.dispatchEvent(new CustomEvent('prisma-script-handoff', {detail: {
        project: openProject.id, text: preview.script, assets: preview.managed_assets,
        line: point?.script_line, anchor: point && `# ${point.scene}/${point.index}`,
      }}));
    }
    goWorkspace('script-ide-workspace');
  };
  document.addEventListener('prisma-line-production', (event) => refresh(event.detail));
  get('point').onchange = () => selectPoint(Number(value('point')), true);
  get('action').onchange = () => updateForm();
  get('asset').onchange = updateMedia;
  get('replace').onchange = () => {
    const index = value('replace');
    if (index === '') return;
    const [kind, current] = Object.entries(point.cues[Number(index)])[0];
    get('action').value = kind === 'music' && current === null ? 'stop' : kind;
    if (kind === 'show' || kind === 'hide') get('actor').value = kind === 'show' ? current.actor : current;
    updateForm(kind === 'show' ? current.file : current);
  };

  get('bind').onsubmit = async (event) => {
    event.preventDefault();
    const submitted = input();
    if (running || !point || JSON.stringify(submitted) !== renderedInput) return;
    const kind = value('action');
    const file = value('asset');
    const actor = value('actor').trim();
    if (!get('asset-row').hidden && !file) return status('请选择素材。', true);
    if (!get('actor-row').hidden && !actor) return status('请填写角色名。', true);
    const cue = kind === 'show' ? {show: {actor, file}} : kind === 'hide' ? {hide: actor}
      : kind === 'stop' ? {music: null} : {[kind]: file};
    const selected = Number(value('point'));
    running = true;
    updateForm();
    try {
      const response = await request('/api/script/bind', {...submitted, cue, scene: point.scene, index: point.index,
        line_id: point.id || undefined, replace: value('replace') === '' ? undefined : Number(value('replace'))});
      const result = await response.json();
      if (JSON.stringify(submitted) !== JSON.stringify(input())) return;
      cues.value = JSON.stringify(result.cues, null, 2);
      if (scenario.value !== JSON.stringify(result.scenario)) showScenario(result.scenario, point.scene);
      cues.dispatchEvent(new Event('input'));
      render(result, selected);
    } catch (error) { status(error.message, true); }
    finally { running = false; updateForm(); }
  };

  function invalidate() {
    if (preview && renderedInput !== JSON.stringify(input())) {
      status('编辑内容已变化，请重新预览。');
    }
    updateForm();
  }
  cues.addEventListener('input', invalidate);
  font.addEventListener('change', invalidate);
  document.addEventListener('prisma-editors-change', invalidate);
  document.addEventListener('prisma-assets-change', invalidate);
  document.addEventListener('prisma-project-change', () => {
    preview = point = null;
    get('code').replaceChildren();
    get('point').replaceChildren();
    get('replace').replaceChildren(new Option('新增动作', ''));
    get('location').textContent = '尚未选择台词';
    status('生成预览后，可在代码里点选台词并插入素材。');
    updateForm();
  });
  updateForm();
})();
