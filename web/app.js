const field = (id) => document.getElementById(id);
const raw = field('raw');
const scenario = field('scenario');
const status = field('status');
const sceneSelect = field('scene');
const action = field('action');
const asset = field('asset');
const font = field('font');
const cues = field('cues');
const lineCues = 'by_line_id';
const uploads = new Map();
const managedAssets = new WeakMap();
const scenarioData = () => scenario.value ? JSON.parse(scenario.value) : undefined;
const llmSettingSuffixes = {mode: 'mode', temperature: 'temperature',
  max_tokens: 'tokens', timeout: 'timeout', system_prompt: 'system'};
const projectConfigIds = [
  'project-width', 'project-height', 'config-llm-mode',
  'config-llm-temperature', 'config-llm-tokens', 'config-llm-timeout', 'config-llm-system',
  'config-llm-scenario-enabled', 'config-llm-scenario-mode',
  'config-llm-scenario-temperature', 'config-llm-scenario-tokens', 'config-llm-scenario-timeout',
  'config-llm-scenario-system', 'config-image-mode', 'config-image-base',
  'config-image-lora', 'config-image-compare', 'config-image-weight', 'config-image-size',
  'config-image-sampler', 'config-image-steps', 'config-image-cfg', 'config-image-seed',
  'config-image-count', 'config-image-online-size', 'config-image-quality',
  'config-image-negative', 'config-train-base', 'config-train-dataset', 'config-train-parent',
  'config-train-name', 'config-train-steps', 'config-train-save', 'config-train-rank',
  'config-train-lr', 'config-train-repeats', 'config-train-seed', 'config-prompt-style', 'config-prompt-negative',
];
let openProject = null;
let projectScopeId;
let pendingProjectView = '';
let projectWriteTimer;
let projectWriteInFlight = false;
let projectWritePromise;
let failedWriteFingerprint = null;
let projectHistoryItems = [];
let scenarioLlmOverrideInitialized = false;
const projectScope = () => openProject ? {project_id: openProject.id} : {};

function updateProjectScope() {
  const current = field('project-current');
  current.textContent = openProject ? field('project-name').value || '未命名项目' : '未打开项目';
  current.title = current.textContent;
  field('project-scope').hidden = !openProject;
  if (!openProject) setProjectNavOpen(false);
  updateWorkspaceTitle(document.querySelector('[data-workspace-view]:not([hidden])'));
  updateWorkspaceStatus();
  field('project-close').disabled = !openProject;
  if (projectScopeId !== openProject?.id) {
    projectScopeId = openProject?.id;
    document.dispatchEvent(new Event('prisma-project-change'));
  }
}

function updateWorkspaceTitle(view) {
  if (!view) return;
  const title = view.dataset.workspaceView;
  field('workspace-title').textContent = title;
  const scope = openProject && view.id !== 'project-manager' ? `${field('project-current').textContent} · ` : '';
  document.title = `${scope}${title} · Prisma`;
}

function uiIcon(name) {
  const namespace = 'http://www.w3.org/2000/svg';
  const icon = document.createElementNS(namespace, 'svg');
  icon.classList.add('ui-icon');
  icon.setAttribute('aria-hidden', 'true');
  icon.setAttribute('focusable', 'false');
  const use = document.createElementNS(namespace, 'use');
  use.setAttribute('href', `#icon-${name}`);
  icon.append(use);
  return icon;
}
let editorBaseline = null;
let exported = false;
const imageExtensions = /\.(png|jpe?g|webp)$/i;
const audioExtensions = /\.(ogg|opus|mp3|wav|flac)$/i;

function scriptInputs() {
  const assetIds = Object.fromEntries([...uploads].map(([name, file]) => [name, managedAssets.get(file)]));
  return {scenario: scenarioData(), cues: cues.value, font: font.value, config: projectConfig(),
    project_title: field('project-name').value, asset_ids: assetIds, ...projectScope()};
}
const fontExtensions = /\.(ttf|otf|ttc)$/i;
const sample = `# 第一章　末班车
$[林岚]

雨敲着玻璃。
我抬眼，看见周明站在门边。
「你还知道回来。」
「末班车还没走。」

---

走出车站时，雨已经停了。`;

let scenes = [];
let selectedLine = null;
let inspectedText = null;
let previewedAsset = null;
const pipeline = field('pipeline');
let pipelineSummaries = {};
let pipelineAvailableSnapshots = {};
let nodeInputs = [];

function message(text, isError = false) {
  status.textContent = text || '就绪';
  status.title = status.textContent;
  status.classList.toggle('error', isError);
  status.setAttribute('role', isError ? 'alert' : 'status');
}

function editorFingerprint() {
  return JSON.stringify([
    field('project-name').value, raw.value, scenario.value, cues.value, font.value,
    projectConfigIds.map((id) => {
      const control = field(id);
      return control instanceof HTMLInputElement && control.type === 'checkbox' ? control.checked : control.value;
    }),
    field('heading').value, field('speakers').value,
    [...uploads].map(([name, file]) => [name, managedAssets.get(file)]),
  ]);
}

function projectConfig() {
  const width = field('project-width').valueAsNumber;
  const height = field('project-height').valueAsNumber;
  if (!Number.isInteger(width) || width < 640 || width > 7680
      || !Number.isInteger(height) || height < 360 || height > 4320) {
    throw new Error('项目分辨率须为整数，宽度 640–7680，高度 360–4320。');
  }
  const value = (id) => field(id).value;
  const number = (id) => field(id).valueAsNumber;
  const seed = (id) => value(id) === '' ? null : number(id);
  const readLlmSettings = (prefix) => ({
    mode: value(`${prefix}mode`),
    temperature: number(`${prefix}temperature`), max_tokens: number(`${prefix}tokens`),
    timeout: number(`${prefix}timeout`), system_prompt: value(`${prefix}system`),
  });
  const llm = readLlmSettings('config-llm-');
  const scenarioLlm = readLlmSettings('config-llm-scenario-');
  const config = {
    resolution: {width, height},
    llm,
    llm_overrides: {scenario: {enabled: field('config-llm-scenario-enabled').checked,
      settings: scenarioLlmOverrideInitialized ? scenarioLlm : {}}},
    generation: {mode: value('config-image-mode'), base_model: value('config-image-base'),
      lora: value('config-image-lora'), compare: value('config-image-compare'), weight: number('config-image-weight'),
      size: value('config-image-size'), seed: seed('config-image-seed'), sampler: value('config-image-sampler'),
      steps: number('config-image-steps'), cfg: number('config-image-cfg'), count: number('config-image-count'),
      negative: value('config-image-negative'),
      online_size: value('config-image-online-size'), quality: value('config-image-quality')},
    training: {base_model: value('config-train-base'), dataset: value('config-train-dataset'),
      parent: value('config-train-parent'), name: value('config-train-name'), steps: number('config-train-steps'),
      save_every: number('config-train-save'), rank: number('config-train-rank'), lr: number('config-train-lr'),
      repeats: number('config-train-repeats'), seed: number('config-train-seed')},
    prompts: {style: value('config-prompt-style'), negative: value('config-prompt-negative')},
  };
  const {generation, training, prompts} = config;
  const numericValid = (number, min, max, integer = false) => Number.isFinite(number)
    && number >= min && number <= max && (!integer || Number.isInteger(number));
  validateLlmConfig(llm, '项目全局 LLM', numericValid);
  if (config.llm_overrides.scenario.enabled) {
    validateLlmConfig({...llm, ...scenarioLlm}, 'Scenario LLM Override', numericValid);
  }
  if (!numericValid(generation.weight, 0.05, 1.5) || !numericValid(generation.steps, 1, 60, true)
      || !numericValid(generation.cfg, 1, 15) || !numericValid(generation.count, 1, 4, true)
      || (generation.seed !== null && !numericValid(generation.seed, 0, 2147483644, true))) {
    throw new Error('图像生成参数超出有效范围。');
  }
  if (!numericValid(training.steps, 1, 5000, true) || !numericValid(training.save_every, 1, 5000, true)
      || !numericValid(training.rank, 2, 64, true) || !numericValid(training.lr, 0.000001, 0.001)
      || !numericValid(training.repeats, 1, 50, true) || !numericValid(training.seed, 0, 2147483647, true)) {
    throw new Error('LoRA 训练参数超出有效范围。');
  }
  if (prompts.style.length > 2000 || prompts.negative.length > 2000 || generation.negative.length > 4000) {
    throw new Error('提示词内容超过长度限制。');
  }
  return config;
}

function validateLlmConfig(llm, label, numericValid) {
  if (!['rules', 'openai-compatible'].includes(llm.mode)) {
    throw new Error(`${label}请选择执行方式。`);
  }
  if (!numericValid(llm.temperature, 0, 2) || !numericValid(llm.max_tokens, 128, 32768, true)
      || !numericValid(llm.timeout, 30, 600, true) || !llm.system_prompt.trim() || llm.system_prompt.length > 6000) {
    throw new Error(`${label} Temperature、Token 上限、请求超时或系统提示词无效。`);
  }
}

function copyGlobalLlmToScenarioOverride() {
  for (const suffix of Object.values(llmSettingSuffixes)) {
    const source = field(`config-llm-${suffix}`), target = field(`config-llm-scenario-${suffix}`);
    if (target instanceof HTMLSelectElement && !Array.from(target.options).some((option) => option.value === source.value)) {
      target.add(new Option(source.selectedOptions[0]?.textContent || source.value, source.value));
    }
    target.value = source.value;
  }
}

function updateScenarioLlmOverrideUi() {
  const enabled = field('config-llm-scenario-enabled').checked;
  const controls = field('config-llm-scenario-fields');
  controls.hidden = !enabled;
  controls.querySelectorAll('input, select, textarea').forEach((control) => { control.disabled = !enabled; });
}

function applyProjectConfig(config = {}) {
  const defaults = {
    resolution: {width: 1280, height: 720},
    llm: {mode: 'rules', provider_id: '', base_url: '', model: '', temperature: 0.2,
      max_tokens: 4096, timeout: 180, system_prompt: field('config-llm-system').defaultValue},
    generation: {mode: 'local', base_model: '', lora: '', compare: 'none', weight: 0.35, size: '512x896',
      seed: null, sampler: 'euler_a', steps: 20, cfg: 6.5, count: 1,
      negative: field('config-image-negative').defaultValue, provider_id: '', online_model: '', online_size: '1024x1536', quality: 'medium'},
    training: {base_model: '', dataset: '', parent: '', name: 'prisma_style_next', steps: 200,
      save_every: 100, rank: 4, lr: 0.0001, repeats: 10, seed: 1065270805},
    prompts: {style: field('config-prompt-style').defaultValue, negative: field('config-prompt-negative').defaultValue},
    llm_overrides: {scenario: {enabled: false, settings: {}}},
  };
  const merged = Object.fromEntries(Object.entries(defaults).map(([key, base]) => [key, {...base, ...(config[key] || {})}]));
  const set = (id, value) => {
    const element = field(id);
    const normalized = value == null ? '' : String(value);
    if (element instanceof HTMLSelectElement && normalized && ![...element.options].some((option) => option.value === normalized)) {
      element.add(new Option(normalized, normalized));
    }
    element.value = normalized;
  };
  set('project-width', merged.resolution.width);
  set('project-height', merged.resolution.height);
  const groups = {llm: 'config-llm-', generation: 'config-image-', training: 'config-train-', prompts: 'config-prompt-'};
  for (const [group, prefix] of Object.entries(groups)) {
    const keys = group === 'llm' ? llmSettingSuffixes
      : group === 'generation' ? {mode: 'mode', base_model: 'base', lora: 'lora', compare: 'compare', weight: 'weight',
        size: 'size', seed: 'seed', sampler: 'sampler', steps: 'steps', cfg: 'cfg', count: 'count', negative: 'negative',
        online_size: 'online-size', quality: 'quality'}
      : group === 'training' ? {base_model: 'base', dataset: 'dataset', parent: 'parent', name: 'name', steps: 'steps',
        save_every: 'save', rank: 'rank', lr: 'lr', repeats: 'repeats', seed: 'seed'}
      : {style: 'style', negative: 'negative'};
    for (const [key, suffix] of Object.entries(keys)) set(prefix + suffix, merged[group][key]);
  }
  const scenarioOverride = merged.llm_overrides.scenario || {};
  const scenarioSettings = {...merged.llm, ...(scenarioOverride.settings || {})};
  set('config-llm-scenario-enabled', scenarioOverride.enabled === true);
  for (const [key, suffix] of Object.entries(llmSettingSuffixes)) {
    set(`config-llm-scenario-${suffix}`, scenarioSettings[key]);
  }
  scenarioLlmOverrideInitialized = Object.keys(scenarioOverride.settings || {}).length > 0;
  if (field('config-llm-scenario-enabled').checked && !scenarioLlmOverrideInitialized) {
    copyGlobalLlmToScenarioOverride();
    scenarioLlmOverrideInitialized = true;
  }
  updateScenarioLlmOverrideUi();
}

function projectHasEdits() {
  return editorBaseline !== null && editorFingerprint() !== editorBaseline;
}

function allowProjectSwitch() {
  return true;
}

function cueReferences(plan) {
  let count = 0;
  const names = new Set(font.value ? [font.value] : []);
  for (const scene of Object.values(plan)) {
    if (!scene || typeof scene !== 'object' || Array.isArray(scene)) throw new Error('每组演出绑定须为 JSON 对象。');
    for (const commands of Object.values(scene)) {
      if (!Array.isArray(commands)) throw new Error('每个文本条目的演出动作须为数组。');
      for (const cue of commands) {
        if (!cue || typeof cue !== 'object' || Array.isArray(cue)) throw new Error('每个演出动作须为对象。');
        count++;
        for (const [kind, value] of Object.entries(cue)) {
          const name = kind === 'show' ? value?.file : value;
          if (['background', 'show', 'music', 'sound', 'voice'].includes(kind) && typeof name === 'string') {
            names.add(name);
          }
        }
      }
    }
  }
  return {count, names};
}

function enablePipelinePanning() {
  const viewport = pipeline.querySelector('.flow-graph-scroll');
  let pan = null;

  viewport.addEventListener('pointerdown', (event) => {
    if (event.pointerType !== 'mouse' || event.button !== 0 || !event.target.closest('.flow-graph')) return;
    pan = {id: event.pointerId, x: event.clientX, y: event.clientY,
      left: viewport.scrollLeft, top: viewport.scrollTop, dragging: false};
  });

  viewport.addEventListener('pointermove', (event) => {
    if (!pan || event.pointerId !== pan.id || !(event.buttons & 1)) return;
    const dx = event.clientX - pan.x;
    const dy = event.clientY - pan.y;
    if (!pan.dragging) {
      if (Math.hypot(dx, dy) < 5) return;
      // Capturing after the threshold keeps ordinary button clicks on their nodes.
      viewport.setPointerCapture(event.pointerId);
      pan.dragging = true;
      viewport.classList.add('is-panning');
    }
    viewport.scrollLeft = pan.left - dx;
    viewport.scrollTop = pan.top - dy;
  });

  function finishPan(event) {
    if (event.pointerId !== pan?.id) return;
    pan = null;
    viewport.classList.remove('is-panning');
  }

  viewport.addEventListener('pointerup', finishPan);
  viewport.addEventListener('pointercancel', finishPan);
  viewport.addEventListener('lostpointercapture', finishPan);
  viewport.addEventListener('pointerleave', (event) => {
    if (!pan?.dragging) finishPan(event);
  });
}

function updatePipelineStatus({hasScenario, details, error, missing, ready}) {
  const rawLength = raw.value.trim().length;
  const lines = scenes.flatMap((scene) => scene.lines);
  const speakers = new Set(lines.map((line) => line.speaker).filter((speaker) => speaker && speaker !== '？'));
  const unresolved = lines.filter((line) => line.speaker === '？').length;
  const cueCount = details?.count ?? 0;
  const referenceCount = details?.names.size ?? 0;
  const candidates = field('image-gallery').querySelectorAll('figure').length;
  const sceneSummary = `${scenes.length} 场 · ${lines.length} 条台词 · ${speakers.size} 个说话人`;
  pipelineSummaries = {
    source: rawLength ? `已载入 ${rawLength.toLocaleString()} 字原文。` : '尚未载入原文。',
    scenario: hasScenario ? `${sceneSummary}。有 ${unresolved} 条说话人待确认。` : '尚未生成 Scenario。',
    candidates: candidates ? `当前查看的任务有 ${candidates} 张候选图。` : '',
    assets: `素材列表中有 ${uploads.size} 个文件。`,
    cues: error || `${cueCount} 个动作引用 ${referenceCount} 个文件。${missing.length ? `有 ${missing.length} 个引用文件尚未加入。` : ''}`,
    export: exported ? '已生成并下载。' : unresolved ? `还有 ${unresolved} 条对白需要确认说话人。`
      : ready ? '可构建工程。' : '尚未导出。',
  };
  pipelineAvailableSnapshots = {source: rawLength > 0, scenario: hasScenario, assets: uploads.size > 0, cues: cueCount > 0};
  document.dispatchEvent(new Event('prisma-pipeline-activity'));
}

function updateWorkspaceStatus() {
  const dirty = projectHasEdits();
  field('project-write').hidden = !openProject;
  field('project-write').disabled = !dirty || projectWriteInFlight;
  field('project-undo').hidden = !openProject;
  field('project-history-open').hidden = !openProject;
  field('project-undo').disabled = projectHistoryItems.length < 2 || projectWriteInFlight;
  field('project-keep').hidden = Boolean(openProject) || !dirty;
  const saveState = field('project-write-state');
  saveState.classList.toggle('is-dirty', dirty);
  saveState.textContent = openProject
    ? projectWriteInFlight ? '正在写入…' : dirty ? '等待自动写入' : '已自动写入'
    : dirty ? '当前内容还未加入项目' : '';
  let configError = '';
  try { projectConfig(); } catch (error) { configError = error.message; }
  field('project-config-error').textContent = configError;
  field('project-config-error').hidden = !configError;

  let details;
  let error = '';
  try { details = cueReferences(mapping(cues.value, '演出 JSON')); }
  catch (cause) { error = cause.message; }
  const missing = details ? [...details.names].filter((name) => !managedAssets.get(uploads.get(name))) : [];
  const hasScenario = Boolean(scenario.value);
  const scriptRef = nodeInputs.find((item) => item.kind === 'script');
  const exportStage = location.hash === '#stage-export';
  const ready = exportStage ? Boolean(scriptRef) : hasScenario && !error && !missing.length;
  updatePipelineStatus({hasScenario, details, error, missing, ready});
  const row = (label, text, link) => {
    const item = document.createElement('li');
    const key = document.createElement('strong');
    key.textContent = label;
    item.append(key, document.createTextNode(text));
    if (link) {
      const action = document.createElement('a');
      action.href = link;
      action.textContent = '前往处理';
      item.append(action);
    }
    return item;
  };
  field('export-state').textContent = exported ? '已下载工程。' : ready ? '可以导出。' : '请先处理下列项目。';
  field('export-checklist').replaceChildren(
    row('剧本', scenario.value ? `${scenes.length} 场` : '尚未生成或导入', scenario.value ? '' : '#script-editor'),
    row('演出', error || `${details?.count ?? 0} 个动作`, error ? '#cue-panel' : ''),
    row('素材', missing.length ? `缺少 ${missing.join('、')}` : `${details?.names.size ?? 0} 个引用`, missing.length ? '#stage-assets' : ''),
  );
  if (exportStage) field('export-checklist').replaceChildren(row('脚本版本',
    scriptRef ? `已通过 · ${scriptRef.id.slice(0, 10)}` : '请先通过脚本版本', scriptRef ? '' : '#stage-script'));
  field('build').disabled = !ready || field('build').dataset.busy === 'true';
  scheduleProjectWrite(Boolean(configError));
}

function scheduleProjectWrite(invalidConfig = false) {
  clearTimeout(projectWriteTimer);
  if (!openProject || !projectHasEdits() || invalidConfig || projectWriteInFlight
      || failedWriteFingerprint === editorFingerprint()) return;
  projectWriteTimer = setTimeout(async () => {
    projectWriteInFlight = true;
    updateWorkspaceStatus();
    const attemptFingerprint = editorFingerprint();
    projectWritePromise = (async () => {
      try {
        await saveProject();
        failedWriteFingerprint = null;
      } catch (error) {
        failedWriteFingerprint = attemptFingerprint;
        message(`自动写入失败：${error.message}`, true);
        field('project-write-state').textContent = '自动写入失败';
        field('project-write-state').classList.add('is-dirty');
      } finally {
        projectWriteInFlight = false;
        updateWorkspaceStatus();
      }
    })();
    await projectWritePromise;
  }, 1500);
}

async function flushProjectWrite() {
  clearTimeout(projectWriteTimer);
  while (projectWriteInFlight) await projectWritePromise;
  clearTimeout(projectWriteTimer);
  if (openProject && projectHasEdits()) {
    projectWriteInFlight = true;
    updateWorkspaceStatus();
    try { await saveProject(); }
    finally {
      projectWriteInFlight = false;
      updateWorkspaceStatus();
    }
  }
}

function workspaceRoute(id) {
  if (id === 'script-ide') return 'script-ide-workspace';
  return ['script-workbench', 'production'].includes(id) ? 'stage-script' : id;
}

function projectAddress(id) {
  const address = new URL(location.href);
  if (id) address.searchParams.set('project', id);
  else address.searchParams.delete('project');
  if (address.href !== location.href) history.replaceState(null, '', address);
}

function revealActiveRouteNode() {
  const nodes = field('route-nav-children');
  const current = nodes.querySelector('[aria-current="page"]');
  if (!current || !nodes.clientHeight) return;
  const item = current.getBoundingClientRect();
  const list = nodes.getBoundingClientRect();
  if (item.top < list.top) nodes.scrollTop += item.top - list.top;
  else if (item.bottom > list.bottom) nodes.scrollTop += item.bottom - list.bottom;
}

function setProjectNavOpen(open) {
  field('project-nav').toggleAttribute('data-expanded', open);
  field('project-nav-toggle').setAttribute('aria-expanded', String(open));
  if (open) revealActiveRouteNode();
}

function setRouteNavOpen(open) {
  const toggle = field('route-nav-toggle');
  field('route-nav-children').hidden = !open;
  toggle.setAttribute('aria-expanded', String(open));
  toggle.setAttribute('aria-label', open ? '折叠路线图节点' : '展开路线图节点');
}

function syncNodeNavigation(nodes) {
  const menu = field('route-nav-children');
  const routes = nodes.map((node) => `#stage-${node.id}`);
  if (Array.from(menu.querySelectorAll('a'), (link) => link.hash).join() === routes.join()) return;
  menu.replaceChildren(...nodes.map((node, index) => {
    const link = document.createElement('a');
    link.href = routes[index];
    link.textContent = node.title;
    if (link.hash === location.hash) link.setAttribute('aria-current', 'page');
    return link;
  }));
}

function workspaceView() {
  const hash = location.hash.slice(1);
  let id;
  try { id = decodeURIComponent(hash); }
  catch { id = ''; }
  const original = id;
  id = workspaceRoute(id);
  if (openProject) projectAddress(openProject.id);
  if (openProject && id !== original) history.replaceState(null, '', `#${id}`);
  const stage = /^stage-([a-z][a-z0-9-]*)$/.exec(id);
  document.dispatchEvent(new CustomEvent('prisma-before-view-change', {detail: stage?.[1] || null}));
  let target = field(stage ? 'pipeline-stage' : id || 'projects');
  let view = target?.closest('[data-workspace-view]') || field('projects');
  // Production surfaces need an opened project; the project library and
  // workspace-level API provider manager are global.
  if (!['project-manager', 'api-providers', 'plugin-manager'].includes(view.id) && !openProject) {
    if (stage || ['projects', 'script-ide-workspace', 'approved-assets', 'project-config'].includes(id)) pendingProjectView = id;
    view = field('project-manager');
    target = view;
    if (location.hash !== '#project-manager') history.replaceState(null, '', '#project-manager');
  }
  document.querySelectorAll('[data-workspace-view]').forEach((panel) => { panel.hidden = panel !== view; });
  field('workspace-content').classList.toggle('workspace-content-ide', view.id === 'script-ide-workspace');
  const awaitingProject = !openProject && Boolean(pendingProjectView);
  let activeTab = view.id === 'pipeline-stage' ? id : view.id;
  if (awaitingProject) activeTab = pendingProjectView;
  const insideRoute = activeTab.startsWith('stage-');
  field('route-nav').toggleAttribute('data-active', insideRoute);
  if (insideRoute) setRouteNavOpen(true);
  const hint = field('project-navigation-hint');
  hint.hidden = !awaitingProject;
  if (awaitingProject) hint.textContent = '打开一个项目即可进入所选节点。';
  setProjectNavOpen(false);
  document.querySelectorAll('.workspace-nav a').forEach((link) => {
    if (link.hash === `#${activeTab}`) {
      link.setAttribute('aria-current', 'page');
      if (awaitingProject) hint.textContent = `打开一个项目即可进入「${link.textContent.trim()}」。`;
    } else link.removeAttribute('aria-current');
  });
  if (insideRoute) revealActiveRouteNode();
  document.querySelectorAll('.config-nav a').forEach((link) => {
    if (link.hash === `#${target?.id}`) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  });
  updateWorkspaceTitle(view);
  field('workspace-map-link').hidden = ['projects', 'project-manager', 'pipeline-stage', 'api-providers', 'plugin-manager'].includes(view.id);
  if (stage && openProject) document.dispatchEvent(new CustomEvent('prisma-stage-change', {detail: stage[1]}));
  if (target && target !== view && target.id !== 'pipeline') {
    const disclosure = target.closest('details');
    if (disclosure) disclosure.open = true;
    target.scrollIntoView({block: 'start', inline: 'nearest'});
  } else field('workspace-content').scrollTo(0, 0);
  document.dispatchEvent(new CustomEvent('prisma-view-change', {detail: view.id}));
}

function goWorkspace(id) {
  id = workspaceRoute(id);
  const target = field(id);
  if (target && !field('pipeline-stage').hidden && field('stage-tools').contains(target)) {
    const disclosure = target.closest('details');
    if (disclosure) disclosure.open = true;
    target.scrollIntoView({block: 'start', inline: 'nearest'});
    return;
  }
  if (location.hash !== `#${id}`) history.pushState(null, '', `#${id}`);
  workspaceView();
}

window.addEventListener('hashchange', workspaceView);
field('nav-project-library').addEventListener('click', () => {
  pendingProjectView = '';
  if (location.hash === '#project-manager') workspaceView();
});
field('project-nav-toggle').addEventListener('click', (event) => {
  setProjectNavOpen(event.currentTarget.getAttribute('aria-expanded') !== 'true');
});
field('route-nav-toggle').addEventListener('click', (event) => {
  const open = event.currentTarget.getAttribute('aria-expanded') !== 'true';
  setRouteNavOpen(open);
  if (open) revealActiveRouteNode();
});
field('project-nav').addEventListener('keydown', (event) => {
  const toggle = field('project-nav-toggle');
  if (event.key !== 'Escape' || toggle.getAttribute('aria-expanded') !== 'true') return;
  setProjectNavOpen(false);
  toggle.focus();
});
document.addEventListener('prisma-view-change', (event) => {
  if (event.detail === 'project-config') refreshProjectLlmStatus();
});

function mapping(value, name) {
  let result;
  try {
    result = JSON.parse(value.trim() || '{}');
  } catch {
    throw new Error(`${name}格式有误，请检查 JSON。`);
  }
  if (!result || typeof result !== 'object' || Array.isArray(result)) {
    throw new Error(`${name}需要一个 JSON 对象。`);
  }
  return result;
}

async function request(path, data) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error);
  }
  return response;
}

async function approvedNodeInput(kind) {
  const reference = nodeInputs.find((item) => item.kind === kind);
  if (!reference) return null;
  const response = await request('/api/images/library/item', {kind, id: reference.id});
  return (await response.json()).metadata;
}

function reportNodeOutput(node, result) {
  document.dispatchEvent(new CustomEvent('prisma-node-output', {detail: {node, result}}));
}


async function run(button, pending, task) {
  button.disabled = true;
  button.dataset.busy = 'true';
  message(pending);
  try {
    await task();
  } catch (error) {
    message(error.message, true);
  } finally {
    delete button.dataset.busy;
    button.disabled = false;
    updateWorkspaceStatus();
  }
}

const requestLogNodes = {'scenario': 'scenario-request-log', 'voice-assets': 'voice-request-log'};
const requestLogWatchers = new Map();
const runStates = {running: '运行中', done: '已完成', failed: '失败', interrupted: '服务重启，运行已中断'};
const requestPhases = {connecting: '连接中', waiting: '等待响应正文', stream: '正在接收输出',
  received: '已收到响应', validating: '正在校验', completed: '请求完成', failed: '请求失败',
  retrying: '切换为普通响应', interrupted: '已中断', cached: '命中缓存'};

function tokenSummary(usage = {}) {
  return Object.entries({input_tokens: '输入', output_tokens: '输出', reasoning_tokens: '思考',
    cache_read_input_tokens: '读取缓存', cache_creation_input_tokens: '写入缓存', total_tokens: '合计'})
    .filter(([key]) => usage[key] != null)
    .map(([key, label]) => `${label} ${usage[key].toLocaleString()} tokens`);
}

async function readRequestEvidence(node, runId, requestId, output) {
  const project = openProject?.id;
  output.hidden = false;
  output.textContent = '正在读取…';
  try {
    const {run} = await (await request('/api/generation/log', {
      project_id: project, node, run_id: runId, request_id: requestId, include_response: true,
    })).json();
    if (openProject?.id === project && output.dataset.run === runId) {
      output.textContent = run?.response_text || '没有可显示的响应正文。';
    }
  } catch (error) {
    if (openProject?.id === project && output.dataset.run === runId) output.textContent = error.message;
  }
}

function renderGenerationRequests(node, panel, run) {
  const list = panel.querySelector('.request-log-requests');
  const usage = panel.querySelector('.request-log-summary');
  const requests = run?.requests || [];
  const tokens = tokenSummary(run?.usage);
  const requested = requests.filter((item) => item.phase !== 'cached');
  const cacheHits = requests.length - requested.length;
  usage.hidden = !requests.length;
  usage.textContent = [requested.length + ' 次模型调用', cacheHits ? cacheHits + ' 批命中缓存' : '',
    ...tokens].filter(Boolean).join(' · ');
  const previous = new Map([...list.children].map((item) => [item.dataset.request, item]));
  const active = requested.find((item) => ['connecting', 'waiting', 'stream', 'retrying', 'validating'].includes(item.phase));
  for (const detail of requests) {
    const {context, usage: measured, provider, model, phase} = detail;
    const existing = previous.get(detail.id);
    if (existing) previous.delete(detail.id);
    const row = existing || document.createElement('details');
    if (!existing) {
      row.dataset.request = detail.id;
      const heading = document.createElement('summary');
      const numbers = document.createElement('p');
      numbers.className = 'hint';
      const message = document.createElement('p');
      const preview = document.createElement('pre');
      preview.setAttribute('aria-label', '模型正文末尾预览');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '查看失败响应';
      const evidence = document.createElement('pre');
      evidence.hidden = true;
      evidence.dataset.run = run.id;
      button.onclick = () => readRequestEvidence(node, run.id, detail.id, evidence);
      row.append(heading, numbers, message, preview, button, evidence);
      row.open = detail.id === active?.id || (phase === 'failed' && requested.length === 1);
      list.append(row);
    }
    const [heading, numbers, message, preview, evidenceButton] = row.children;
    const label = context.chapter
      ? `第 ${context.number} 章 · 第 ${context.batch} 批 · 原文 ${context.first_line}–${context.last_line} 行`
      : context.label;
    const elapsed = ['connecting', 'waiting', 'stream', 'retrying', 'validating'].includes(phase)
      && run.state === 'running' ? Math.max(detail.elapsed, (Date.now() - Date.parse(detail.at)) / 1000) : detail.elapsed;
    heading.textContent = [label, provider && `${provider} / ${model}`, requestPhases[phase]].filter(Boolean).join(' · ');
    numbers.textContent = phase === 'cached' ? '' : [
      `${elapsed.toFixed(1)} 秒`, detail.first_output != null ? `首段 ${detail.first_output.toFixed(1)} 秒` : '',
      detail.operation, detail.status ? 'HTTP ' + detail.status : '', '请求 ' + detail.id.slice(0, 8),
      detail.transport === 'stream' ? '流式' : detail.transport === 'buffered' ? '普通响应' : '',
      `输入 ${detail.prompt_chars.toLocaleString()} 字符`,
      detail.content_chars != null ? `正文 ${detail.content_chars.toLocaleString()} 字符` : '',
      detail.reasoning_chars != null ? `思考 ${detail.reasoning_chars.toLocaleString()} 字符` : '',
      detail.response_bytes != null ? `响应 ${detail.response_bytes.toLocaleString()} 字节` : '',
      ...tokenSummary(measured), detail.finish_reason ? '停止原因 ' + detail.finish_reason : '',
      detail.max_tokens ? '输出上限 ' + detail.max_tokens : '', '请求超时 ' + detail.timeout + ' 秒',
      detail.attempt > 1 ? '尝试 ' + detail.attempt : '',
    ].filter(Boolean).join(' · ');
    numbers.hidden = !numbers.textContent;
    message.textContent = [detail.error, ...(detail.retries || []).map((retry) => retry.error)].filter(Boolean).join(' · ');
    message.className = phase === 'failed' ? 'error-text' : 'hint';
    message.hidden = !message.textContent;
    preview.hidden = !detail.preview;
    if (preview.textContent !== (detail.preview || '')) preview.textContent = detail.preview || '';
    evidenceButton.hidden = !detail.has_evidence;
  }
  for (const outdated of previous.values()) outdated.remove();
}

function showRequestLog(node, run, fallback = '', history = []) {
  const panel = field(requestLogNodes[node]);
  const summary = panel.querySelector('[role=status]');
  const title = run ? runStates[run.state] : fallback || '暂无运行记录';
  if (summary.textContent !== title) summary.textContent = title;
  summary.classList.toggle('error-text', ['failed', 'interrupted'].includes(run?.state));
  const runs = panel.querySelector('select');
  const signature = JSON.stringify(history);
  if (runs.dataset.signature !== signature) {
    const selected = runs.value;
    runs.replaceChildren(new Option('最近一次', ''), ...history.map(({id, at, state}) =>
      new Option([at && new Date(at).toLocaleString('zh-CN', {hour12: false}), runStates[state], id.slice(0, 8)]
        .filter(Boolean).join(' · '), id)));
    if (history.some(({id}) => id === selected)) runs.value = selected;
    runs.dataset.signature = signature;
    runs.parentElement.hidden = !history.length;
  }
  renderGenerationRequests(node, panel, run);
  if (node === 'scenario') {
    const details = field('scenario-response-preview');
    const id = run?.has_response && !run.requests?.some((item) => item.has_evidence) ? run.id : '';
    if (details.dataset.run !== id) {
      details.dataset.run = id;
      details.open = false;
      details.querySelector('pre').textContent = '';
    }
    details.hidden = !id;
  }
  const output = panel.querySelector('ol');
  const events = run?.events || [];
  const timeline = JSON.stringify(events);
  if (output.dataset.signature === timeline) return;
  output.dataset.signature = timeline;
  output.replaceChildren(...events.map((event) => {
    const row = document.createElement('li');
    const time = document.createElement('time');
    time.dateTime = event.at;
    time.textContent = new Date(event.at).toLocaleTimeString('zh-CN', {hour12: false});
    row.append(time, document.createTextNode(` +${event.elapsed.toFixed(2)} s · ${event.message}`));
    return row;
  }));
}

field('scenario-response-preview').ontoggle = async (event) => {
  const details = event.currentTarget;
  const runId = details.dataset.run;
  const output = details.querySelector('pre');
  if (!details.open || !runId || output.textContent) return;
  output.dataset.run = runId;
  await readRequestEvidence('scenario', runId, undefined, output);
};

function watchRequestLog(node, project, runId, {submitting = false} = {}) {
  const watcher = {submitting};
  requestLogWatchers.set(node, watcher);
  const panel = field(requestLogNodes[node]);
  const current = () => requestLogWatchers.get(node) === watcher && project === openProject?.id;
  const query = {project_id: project, node, run_id: runId || undefined, include_history: true};
  if (submitting) panel.querySelector('[role=status]').textContent = '正在提交请求…';
  const read = async () => {
    if (!current()) return;
    try {
      const {run, history} = await (await request('/api/generation/log', query)).json();
      if (!current() || watcher.finished) return;
      if (run || !submitting) showRequestLog(node, run, '', history);
      if (run?.state === 'running' || (submitting && !run && !watcher.finished)) setTimeout(read, 1100);
      else if (!runId && !panel.closest('[hidden]')) setTimeout(read, 3500);
    } catch (error) {
      if (current() && !watcher.finished) {
        panel.querySelector('[role=status]').textContent = '日志连接中断，正在重连…';
        setTimeout(read, 2500);
      }
    }
  };
  read();
  return async (error) => {
    watcher.finished = true;
    if (!current()) return;
    try {
      const {run, history} = await (await request('/api/generation/log', query)).json();
      if (current()) {
        showRequestLog(node, run, error?.message || '请求已结束。', history);
        watcher.submitting = false;
        if (run) watchRequestLog(node, project);
      }
    } catch (failure) {
      watcher.submitting = false;
      if (current()) showRequestLog(node, null, error?.message || failure.message);
    }
  };
}

async function generationRequest(node, url, data) {
  const run_id = crypto.randomUUID().replaceAll('-', '');
  field(requestLogNodes[node]).querySelector('select').value = '';
  const finish = watchRequestLog(node, data.project_id, run_id, {submitting: true});
  document.dispatchEvent(new Event('prisma-pipeline-activity'));
  try {
    const result = await (await request(url, {...data, run_id})).json();
    await finish();
    return result;
  } catch (error) {
    await finish(error);
    throw error;
  } finally {
    if (data.project_id === openProject?.id) document.dispatchEvent(new Event('prisma-pipeline-refresh'));
  }
}

document.addEventListener('prisma-project-change', () => {
  requestLogWatchers.clear();
  for (const node of Object.keys(requestLogNodes)) showRequestLog(node, null);
});
document.addEventListener('prisma-node-tools', ({detail}) => {
  if (requestLogNodes[detail] && openProject && !requestLogWatchers.get(detail)?.submitting) {
    watchRequestLog(detail, openProject.id, field(requestLogNodes[detail]).querySelector('select').value);
  }
});
for (const [node, id] of Object.entries(requestLogNodes)) {
  field(id).querySelector('select').onchange = (event) => watchRequestLog(node, openProject?.id, event.currentTarget.value);
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const productionRegistryNodes = {'content-overview': ['work-style', '作品风格'],
  'character-overview': ['character-design', '角色登记'], 'background-assets': ['scene-design', '场景背景'],
  'music-assets': ['music', '音乐制作输入'], 'cg-assets': ['cg-design', '事件 CG 声明'],
  'portrait-baseline': ['character-design', '立绘制作名单'],
  'sprite-assets': ['character-design', '立绘差分对象']};
const productionRoleLabels = {primary: '立绘角色', secondary: '次要角色', unreviewed: '待定'};
let productionDraft = null;
let productionScope = '';
document.addEventListener('prisma-project-change', () => {
  productionScope = '';
  productionDraft = null;
  field('production-batch-open').hidden = true;
});

document.addEventListener('prisma-node-tools', async ({detail}) => {
  const project = openProject?.id;
  if (!project) return;
  const route = productionRegistryNodes[detail];
  if (route) {
    const [kind, title] = route;
    const output = field('production-registry-items');
    const status = field('production-registry-status');
    field('production-registry-title').textContent = title;
    output.replaceChildren();
    status.textContent = '正在读取资源…';
    try {
      const portraits_only = detail === 'portrait-baseline' || detail === 'sprite-assets';
      const result = kind === 'music'
        ? await (await request('/api/pipeline/inputs', {project_id: project, node: detail})).json()
        : await (await request('/api/scenario/production/registry', {project_id: project, kind, portraits_only})).json();
      if (openProject?.id !== project || field('pipeline-stage').dataset.node !== detail) return;
      if (kind === 'music') {
        const inputs = result.music_briefs;
        const music = [
          ['整体曲风', inputs['content-overview'].map(({style}) => style)],
          [result.draft_scene_background ? '场景 BGM（草稿）' : '场景 BGM', inputs['background-assets'].filter(({cues}) => cues.length)
            .map(({label, scene, cues}) => `${scene} · ${label}\n${cues.map((cue) => `原文 ${cue.at} · ${cue.direction}`).join('\n')}`)],
          ['角色曲参考', inputs['character-overview'].map(({label, traits}) => [label, ...traits].join(' · '))],
        ];
        status.textContent = '';
        for (const [label, entries] of music) {
          const section = document.createElement('details');
          const heading = document.createElement('summary');
          heading.textContent = label + ' · ' + entries.length + ' 条';
          const body = document.createElement('pre');
          body.className = 'production-record';
          body.textContent = entries.join('\n\n') || '暂无已通过的输入。';
          section.append(heading, body);
          output.append(section);
        }
        return;
      }
      status.textContent = result.items.length ? result.items.length + ' 条记录' : '当前项目暂无记录。';
      for (const item of result.items) {
        const section = document.createElement('details');
        const heading = document.createElement('summary');
        const meta = item.metadata;
        const role = productionRoleLabels[meta.role] || productionRoleLabels.unreviewed;
        heading.textContent = kind === 'character-design' && !portraits_only ? item.label + ' · ' + role : item.label;
        const body = document.createElement('pre');
        body.className = 'production-record';
        const selected = kind === 'scene-design' ? {场景: meta.id, 原文起点: meta.source_line,
          背景提示词: meta.background_prompt, 场景BGM: meta.music}
          : kind === 'cg-design' ? {场景: meta.scene + ' · ' + meta.place, 原文行: meta.at,
            构图: meta.composition, 演出依据: meta.reason, 制作提示词: meta.prompt}
          : kind === 'character-design' ? {分级: role, 分级依据: meta.role_reason,
            外貌: meta.appearance, 性格: meta.traits, 原文: meta.source_lines, 立绘提示词: meta.prompt}
          : {简介: meta.summary, 背景: meta.background, 音乐: meta.music, CG: meta.cg};
        body.textContent = Object.entries(selected).filter(([, value]) => value != null && value !== '').map(([label, value]) =>
          label + '\n' + (typeof value === 'string' ? value : JSON.stringify(value, null, 2))).join('\n\n');
        section.append(heading, body);
        output.append(section);
      }
    } catch (error) { if (openProject?.id === project) status.textContent = error.message; }
  }
  if (detail !== 'scenario' || productionScope === project) return;
  const summary = field('production-batch-summary');
  try {
    const result = await (await request('/api/scenario/production/outline', {project_id: project})).json();
    if (openProject?.id !== project) return;
    productionScope = project;
    field('production-batch-start').replaceChildren(...result.chapters.map((chapter) =>
      new Option(chapter.number + ' · ' + chapter.title, chapter.number)));
    field('production-batch-run').disabled = !result.chapters.length;
    summary.textContent = result.chapters.length
      ? result.chapters.length + ' 章 · 每批 ' + result.defaults.window + ' 行'
      : '请先保存原文。';
  } catch (error) { if (openProject?.id === project) summary.textContent = error.message; }
});

field('production-batch-run').onclick = (event) => run(event.currentTarget, '正在分批制作 Scenario…', async () => {
  const scope = projectScope();
  if (!scope.project_id) return;
  await flushProjectWrite();
  const result = await generationRequest('scenario', '/api/scenario/production/generate', {
    ...scope, chapter_start: Number(field('production-batch-start').value),
    chapter_count: field('production-batch-count').valueAsNumber,
    model_override: readProviderRef(field('scenario-run-model').value),
  });
  if (scope.project_id !== openProject?.id) return;
  productionDraft = result.id;
  field('production-batch-summary').textContent = result.chapters + ' 章 · ' + result.scenes + ' 场 · '
    + result.lines + ' 行 · CG ' + result.cg + ' 处 · 次要角色 ' + result.secondary_characters
    + ' 位 · 待确认对白 ' + result.unresolved_dialogue.length + ' 条';
  field('production-batch-open').hidden = false;
});

field('production-batch-open').onclick = (event) => run(event.currentTarget, '正在登记并打开 IDE…', async () => {
  const scope = projectScope();
  if (!scope.project_id || !productionDraft) return;
  const head = await (await request('/api/script/editor/open', scope)).json();
  await request('/api/scenario/production/publish', {...scope, id: productionDraft, base: head.id});
  if (scope.project_id === openProject?.id) goWorkspace('script-ide-workspace');
});

let scenarioPlacement = null;

function chapterAndScene(document, id) {
  for (const chapter of document?.chapters || []) {
    const scene = chapter.scenes.find((item) => item.id === id);
    if (scene) return {chapter, scene};
  }
  return {};
}

function showScenarioEditor(placement, chapter, line, index = -1, total = 0) {
  scenarioPlacement = placement;
  field('scenario-line-form').hidden = !placement;
  field('open-line-production').hidden = placement !== 'edit';
  field('scenario-chapter-title').value = chapter?.title || '';
  field('scenario-speaker').value = line?.speaker || '';
  field('scenario-line-text').value = line?.text || '';
  field('scenario-viewpoint').value = line?.viewpoint || '';
  field('scenario-direction').value = line?.direction || '';
  field('scenario-source').textContent = line?.source
    ? `${line.source.kind === 'raw' ? '原文' : '旧版剧本'}第 ${line.source.line} 行` : '';
  for (const [name, enabled] of [['up', index > 0], ['down', index >= 0 && index < total - 1], ['delete', index >= 0]]) {
    field(`scenario-line-${name}`).disabled = !enabled;
  }
}

function renderLines(selectedId) {
  const list = field('lines');
  list.replaceChildren();
  const scene = scenes[Number(sceneSelect.value)];
  field('add-scenario-line').disabled = !scene;
  field('add-scenario-scene').disabled = !scene;
  field('scene-empty').hidden = Boolean(scene);
  list.hidden = !scene;
  showScenarioEditor(null);
  if (!scene) return;
  const {chapter} = chapterAndScene(scenarioData(), scene.id);
  let selectedRow;
  scene.lines.forEach((line, index) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'line';
    row.setAttribute('aria-pressed', 'false');
    const number = document.createElement('small');
    number.textContent = `${scene.id}/${index + 1}`;
    const body = document.createElement('span');
    if (line.speaker) {
      const speaker = document.createElement('strong');
      speaker.textContent = `${line.speaker}：`;
      if (line.speaker === '？') speaker.classList.add('unresolved');
      body.append(speaker);
    }
    body.append(line.text);
    row.append(number, body);
    row.addEventListener('click', () => {
      list.querySelector('[aria-pressed="true"]')?.setAttribute('aria-pressed', 'false');
      row.setAttribute('aria-pressed', 'true');
      selectedLine = {scene: scene.id, index: index + 1, speaker: line.speaker, id: line.id};
      if (line.speaker && line.speaker !== '？') field('actor').value = line.speaker;
      showScenarioEditor('edit', chapter, line, index, scene.lines.length);
      refreshCueForm();
      updateWorkspaceStatus();
    });
    if (line.id === selectedId) selectedRow = row;
    list.append(row);
  });
  list.scrollTop = 0;
  if (selectedRow) {
    selectedRow.click();
    selectedRow.scrollIntoView({block: 'nearest'});
  }
}

function showScenario(data, preferredScene, preferredLine) {
  scenario.value = JSON.stringify(data);
  scenes = data.chapters.flatMap((chapter) =>
    chapter.scenes.map((scene) => ({...scene, chapter: chapter.title})));
  inspectedText = scenario.value;
  selectedLine = null;
  exported = false;
  sceneSelect.replaceChildren(...scenes.map((scene, index) => new Option(`${scene.id} · ${scene.chapter}`, index)));
  sceneSelect.disabled = false;
  const active = scenes.findIndex((scene) => scene.id === preferredScene);
  if (active >= 0) sceneSelect.value = String(active);
  renderLines(preferredLine);
  refreshCueForm();
  const pending = scenes.reduce((total, scene) => total + scene.lines.filter((line) => line.speaker === '？').length, 0);
  field('scene-summary').textContent = `${data.chapters.length} 章 · ${scenes.length} 场 · ${pending} 条待校对`;
  updateWorkspaceStatus();
  document.dispatchEvent(new Event('prisma-editors-change'));
  reportNodeOutput('scenario', {captureProject: true, label: '当前 Scenario', fingerprint: editorFingerprint()});
}

function refreshCueForm() {
  const kind = action.value;
  const active = selectedLine && inspectedText === scenario.value;
  const hasFile = kind !== 'hide' && kind !== 'stop';
  const selected = asset.value;
  const extensions = kind === 'background' || kind === 'show' ? imageExtensions : audioExtensions;
  asset.replaceChildren(new Option('选择素材', ''));
  if (hasFile) {
    for (const name of uploads.keys()) {
      if (extensions.test(name)) asset.add(new Option(name, name));
    }
    if ([...asset.options].some((option) => option.value === selected)) asset.value = selected;
  }
  asset.hidden = !hasFile;
  field('actor').hidden = kind !== 'show' && kind !== 'hide';
  field('anchor').textContent = active ? `${selectedLine.scene}/${selectedLine.index}` : '选择一条台词';
  field('add-cue').disabled = !active;
}

function invalidateScenes() {
  scenes = [];
  inspectedText = null;
  selectedLine = null;
  sceneSelect.replaceChildren(new Option('暂无场景'));
  sceneSelect.disabled = true;
  field('scene-summary').textContent = '等待解析';
  renderLines();
  refreshCueForm();
  updateWorkspaceStatus();
  document.dispatchEvent(new Event('prisma-editors-change'));
}

async function importScenario(input) {
  const before = scenario.value;
  const project = openProject?.id;
  const response = await request('/api/scenes', {scenario: input});
  const data = await response.json();
  if (scenario.value !== before || project !== openProject?.id) return;
  showScenario(data.scenario);
}

async function readText(file) {
  return new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer());
}

for (const [id, editor] of [['raw-file', raw], ['scenario-file', scenario], ['cues-file', cues]]) {
  field(id).addEventListener('change', async (event) => {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file) return;
    if (editor.value.trim() && (editor !== cues || editor.value.trim() !== '{}') && !confirm('替换编辑框中的文本？')) return;
    try {
      const content = await readText(file);
      if (editor === scenario) {
        await importScenario(content);
      } else {
        if (editor === cues) mapping(content, '演出 JSON');
        editor.value = content;
        editor.dispatchEvent(new Event('input'));
      }
      message(`已导入 ${file.name}。`);
    } catch (error) {
      message(`无法导入 ${file.name}。${error.message}`, true);
    }
  });
}

field('project-raw-file').addEventListener('change', (event) => {
  const file = event.target.files[0];
  field('project-raw-file-name').textContent = file ? `将导入：${file.name}` : '暂未选择文件';
});

field('raw-append-file').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  const projectId = openProject?.id;
  if (!projectId) return message('请先打开项目，再追加原文。', true);
  try {
    const content = await readText(file);
    if (projectId !== openProject?.id) return;
    const separator = raw.value && !raw.value.endsWith('\n') ? '\n\n' : raw.value ? '\n' : '';
    raw.value += separator + content;
    raw.dispatchEvent(new Event('input'));
    message(`已将「${file.name}」追加到当前项目原文，修改会自动写入历史。${scenario.value ? '现有 Scenario 未改变，如需纳入新增文本请重新生成并检查。' : ''}`);
  } catch (error) {
    message(`无法追加 ${file.name}。${error.message}`, true);
  }
});

raw.addEventListener('input', () => { exported = false; updateWorkspaceStatus(); });
field('sample').addEventListener('click', () => {
  if (raw.value.trim() && !confirm('替换原文和说话人映射？')) return;
  raw.value = sample;
  field('heading').value = '';
  field('speakers').value = '{"6": "林岚", "7": "周明"}';
  exported = false;
  updateWorkspaceStatus();
  goWorkspace('stage-raw');
  message('已载入车站样例。通过后可在 Scenario 节点生成剧本。');
});

let scenarioTrialProject;
function updateScenarioRunMode() {
  const local = field('scenario-run-mode').value === 'rules';
  const controls = field('scenario-run-parameters');
  controls.hidden = controls.disabled = local;
}

function initScenarioTrial() {
  if (scenarioTrialProject === openProject?.id) return;
  const config = projectConfig();
  const override = config.llm_overrides.scenario;
  const settings = override.enabled ? {...config.llm, ...override.settings} : config.llm;
  for (const [key, suffix] of Object.entries(llmSettingSuffixes)) field(`scenario-run-${suffix}`).value = settings[key];
  scenarioTrialProject = openProject?.id;
  updateScenarioRunMode();
}
document.addEventListener('prisma-project-change', () => { scenarioTrialProject = undefined; });
document.addEventListener('prisma-node-tools', ({detail}) => {
  if (detail === 'scenario') {
    try { initScenarioTrial(); } catch (error) { message(error.message, true); }
  }
});
field('scenario-run-mode').onchange = updateScenarioRunMode;

field('draft').addEventListener('click', (event) => {
  if (scenario.value.trim() && !event.currentTarget.dataset.stageRetry && !confirm('用新草稿替换当前 Scenario？')) return;
  exported = false;
  run(event.currentTarget, '正在生成 Scenario…', async () => {
    const previous = scenario.value;
    const project = openProject?.id;
    const input = await approvedNodeInput('source');
    if (project !== openProject?.id) return;
    initScenarioTrial();
    const mode = field('scenario-run-mode').value;
    const parameters = mode === 'openai-compatible' ? {
      temperature: field('scenario-run-temperature').valueAsNumber,
      max_tokens: field('scenario-run-tokens').valueAsNumber,
      timeout: field('scenario-run-timeout').valueAsNumber,
      system_prompt: field('scenario-run-system').value,
    } : {};
    const result = await generationRequest('scenario', '/api/draft', {
      ...projectScope(),
      mode, parameters,
      model_override: mode === 'openai-compatible' ? readProviderRef(field('scenario-run-model').value) : null,
      raw: input?.text ?? raw.value,
      heading: field('heading').value,
      speakers: mapping(field('speakers').value, '说话人映射'),
    });
    if (project !== openProject?.id) return;
    if (scenario.value !== previous) throw new Error('生成期间 Scenario 已编辑，请重新生成或先下载当前稿。');
    showScenario(result.scenario);
    reportNodeOutput('scenario', {reference: {kind: 'scenario', id: result.scenario_id},
      label: '本次生成的 Scenario', fingerprint: editorFingerprint()});
    message(`Scenario 已由${result.model_ref?.model || '本地规则解析'}生成。请校对 ${result.pending.length} 条待确认的对白。`);
  });
});

scenario.addEventListener('input', () => { exported = false; invalidateScenes(); });
cues.addEventListener('input', () => {
  exported = false;
  updateWorkspaceStatus();
  reportNodeOutput('cues', {captureProject: true, label: '当前演出绑定', fingerprint: editorFingerprint()});
});
field('anchor-scenario').addEventListener('click', (event) => run(event.currentTarget, '正在固定演出位置…', async () => {
  const text = scenario.value;
  const plan = cues.value;
  if (!text) throw new Error('请先生成或导入 Scenario。');
  const project = openProject?.id;
  const response = await request('/api/scenario/anchor', {scenario: scenarioData(), cues: plan, ...projectScope()});
  const result = await response.json();
  if (project !== openProject?.id) return;
  if (scenario.value !== text || cues.value !== plan) throw new Error('固定期间剧本或演出已编辑，请重试。');
  cues.value = JSON.stringify(result.cues, null, 2);
  showScenario(result.scenario, selectedLine?.scene, selectedLine?.id);
  message('已固定演出位置。');
}));
field('save-scenario').addEventListener('click', (event) => {
  if (!scenario.value.trim()) return message('请先填写 Scenario。', true);
  run(event.currentTarget, '正在保存 Scenario…', async () => {
    const document = scenarioData();
    await request('/api/scenario/save', {raw: raw.value, scenario: document, ...projectScope()});
    download(new Blob([JSON.stringify(document, null, 2) + '\n'], {type: 'application/json'}), 'scenario.json');
    message('Scenario 已登记并下载。');
  });
});
field('save-cues').addEventListener('click', (event) => run(event.currentTarget, '正在保存演出绑定…', async () => {
  const text = cues.value.trim() || '{}';
  await request('/api/scenario/save', {scenario: scenarioData(), cues: mapping(text, '演出 JSON'), ...projectScope()});
  download(new Blob([text], {type: 'application/json'}), 'cues.json');
  message('演出绑定已登记并下载。');
}));
sceneSelect.addEventListener('change', () => {
  selectedLine = null;
  renderLines();
  refreshCueForm();
  updateWorkspaceStatus();
});
field('open-line-production').onclick = () => {
  if (!selectedLine) return;
  goWorkspace('stage-cues');
  document.dispatchEvent(new CustomEvent('prisma-line-production', {detail: selectedLine.id}));
};

function startScenarioEntry(placement) {
  const active = scenes[Number(sceneSelect.value)];
  const {chapter} = chapterAndScene(scenarioData(), active?.id);
  showScenarioEditor(placement, placement === 'chapter' ? null : chapter);
  field(placement === 'chapter' ? 'scenario-chapter-title' : 'scenario-line-text').focus();
}

for (const placement of ['line', 'scene', 'chapter']) {
  field(`add-scenario-${placement}`).onclick = () => startScenarioEntry(placement);
}

async function stableCues(data) {
  const plan = mapping(cues.value, '演出 JSON');
  if (Object.keys(plan).every((key) => key === lineCues)) return plan;
  const original = cues.value;
  const current = scenario.value;
  const project = openProject?.id;
  const response = await request('/api/scenario/anchor', {scenario: data, cues: plan, ...projectScope()});
  const result = await response.json();
  if (project !== openProject?.id || cues.value !== original || scenario.value !== current) {
    throw new Error('编辑期间项目或演出有变化，请重试。');
  }
  return result.cues;
}

field('scenario-line-form').onsubmit = (event) => {
  event.preventDefault();
  run(event.submitter, '正在更新剧本…', async () => {
    const data = scenarioData() || {version: 1, chapters: []};
    const active = scenes[Number(sceneSelect.value)];
    const currentSelection = selectedLine;
    const title = field('scenario-chapter-title').value.trim();
    const body = field('scenario-line-text').value.trim();
    if (!title || !body) throw new Error('请填写章节标题和文本。');
    let {chapter, scene} = chapterAndScene(data, active?.id);
    if (scenarioPlacement === 'chapter') {
      const id = String(Math.max(0, ...data.chapters.map((item) => Number(item.id))) + 1);
      chapter = {id, title, scenes: []};
      data.chapters.push(chapter);
    }
    if (scenarioPlacement === 'chapter' || scenarioPlacement === 'scene') {
      const number = Math.max(0, ...chapter.scenes.map((item) => Number(item.id.split(':')[1]))) + 1;
      scene = {id: `${chapter.id}:${number}`, lines: []};
      chapter.scenes.push(scene);
    }
    const placement = scenarioPlacement;
    const index = scene.lines.findIndex((item) => item.id === currentSelection?.id);
    const line = placement === 'edit' ? scene.lines[index] : {id: `line-${crypto.randomUUID()}`};
    line.text = body;
    line.speaker = field('scenario-speaker').value.trim() || null;
    for (const key of ['viewpoint', 'direction']) {
      const value = field(`scenario-${key}`).value.trim();
      if (value) line[key] = value;
      else delete line[key];
    }
    chapter.title = title;
    if (placement === 'line') {
      const plan = await stableCues(scenarioData());
      cues.value = JSON.stringify(plan, null, 2);
    }
    if (placement !== 'edit') scene.lines.splice(index < 0 ? scene.lines.length : index + 1, 0, line);
    showScenario(data, scene.id, line.id);
    message('Scenario 已更新。');
  });
};

for (const [name, step] of [['up', -1], ['down', 1], ['delete', 0]]) {
  field(`scenario-line-${name}`).onclick = (event) => run(event.currentTarget, '正在调整台词…', async () => {
    if (!selectedLine) return;
    const data = scenarioData();
    const {chapter, scene} = chapterAndScene(data, selectedLine.scene);
    const id = selectedLine.id;
    const index = scene.lines.findIndex((item) => item.id === id);
    if (index < 0) return;
    const plan = await stableCues(data);
    if (step) {
      const target = index + step;
      if (target < 0 || target >= scene.lines.length) return;
      [scene.lines[index], scene.lines[target]] = [scene.lines[target], scene.lines[index]];
    } else {
      if (data.chapters.length === 1 && chapter.scenes.length === 1 && scene.lines.length === 1) {
        throw new Error('剧本至少需要一条文本。');
      }
      if (plan[lineCues]?.[id]?.length && !confirm('同时删除这条文本的演出绑定？')) return;
      if (plan[lineCues]) delete plan[lineCues][id];
      scene.lines.splice(index, 1);
      if (!scene.lines.length) chapter.scenes = chapter.scenes.filter((item) => item.id !== scene.id);
      if (!chapter.scenes.length) data.chapters = data.chapters.filter((item) => item.id !== chapter.id);
    }
    cues.value = JSON.stringify(plan, null, 2);
    showScenario(data, scene.id, step ? id : scene.lines[index]?.id ?? scene.lines.at(-1)?.id);
  });
}

function previewProjectAsset(name) {
  previewedAsset = uploads.has(name) ? name : null;
  const preview = field('asset-preview');
  preview.replaceChildren();
  for (const button of field('file-list').querySelectorAll('[data-asset-name]')) {
    button.setAttribute('aria-pressed', String(button.dataset.assetName === previewedAsset));
  }
  const title = document.createElement('p');
  title.textContent = previewedAsset || '选择一个素材查看。';
  preview.append(title);
  if (!previewedAsset) return;
  const id = managedAssets.get(uploads.get(name));
  if (!id) return;
  if (imageExtensions.test(name) || audioExtensions.test(name)) {
    const media = document.createElement(imageExtensions.test(name) ? 'img' : 'audio');
    if (media.tagName === 'IMG') media.alt = name;
    else {
      media.controls = true;
      media.preload = 'none';
      media.setAttribute('aria-label', name);
    }
    media.src = `/api/workspace/assets/${encodeURIComponent(id)}?preview=1`;
    preview.append(media);
  }
  const link = document.createElement('a');
  link.href = `/api/workspace/assets/${encodeURIComponent(id)}`;
  link.download = name;
  link.textContent = '下载原文件';
  preview.append(link);
}

function renderFiles() {
  exported = false;
  const list = field('file-list');
  list.replaceChildren();
  const selectedFont = font.value;
  font.replaceChildren(new Option('Ren’Py 默认', ''));
  for (const name of uploads.keys()) {
    const row = document.createElement('div');
    row.className = 'asset-row';
    const select = document.createElement('button');
    select.type = 'button';
    select.dataset.assetName = name;
    select.textContent = name;
    select.onclick = () => previewProjectAsset(name);
    const type = document.createElement('span');
    type.textContent = imageExtensions.test(name) ? '图片' : audioExtensions.test(name) ? '音频' : '字体';
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '移出';
    remove.setAttribute('aria-label', `移出 ${name}`);
    remove.addEventListener('click', () => {
      uploads.delete(name);
      renderFiles();
    });
    row.append(select, type, remove);
    list.append(row);
    if (fontExtensions.test(name)) font.add(new Option(name, name));
  }
  if (!uploads.size) list.textContent = '当前还没有素材。可上传文件，或从下方资源目录加入。';
  previewProjectAsset(previewedAsset);
  if (uploads.has(selectedFont)) font.value = selectedFont;
  refreshCueForm();
  updateWorkspaceStatus();
  document.dispatchEvent(new Event('prisma-assets-change'));
  const stage = field('pipeline-stage');
  if (!stage.hidden && stage.dataset.node === 'assets') {
    reportNodeOutput('assets', {captureProject: true, label: '当前项目素材', fingerprint: editorFingerprint()});
  }
}

async function registerMedia(name, data, scope = projectScope()) {
  const response = await request('/api/workspace/assets/import', {name, ...data, ...scope});
  return response.json();
}

function useManagedMedia(name, id, file = new File([], name)) {
  managedAssets.set(file, id);
  uploads.set(name, file);
}

field('files').addEventListener('change', async (event) => {
  const files = [...event.target.files];
  event.target.value = '';
  const duplicate = files.find((file) => uploads.has(file.name));
  if (duplicate && !confirm(`替换已添加的同名素材，例如 ${duplicate.name}？`)) return;
  const scope = projectScope();
  event.target.disabled = true;
  try {
    for (const file of files) {
      if (file.size > 45 * 1024 * 1024) throw new Error(`素材「${file.name}」超过 45 MiB。`);
      const resource = await registerMedia(file.name, {data: await encode(file)}, scope);
      if (scope.project_id !== openProject?.id) return;
      useManagedMedia(file.name, resource.id, file);
    }
    if (files.length) message('素材已登记到资源目录。');
  } catch (error) { message(error.message, true); }
  finally { renderFiles(); event.target.disabled = false; }
});

let showProjectTrash = false;
let projectPageOffset = 0;
let projectListRequest = 0;

async function refreshProjects() {
  const sequence = ++projectListRequest;
  const trash = showProjectTrash;
  const response = await request('/api/workspace/project/list', {trash, offset: projectPageOffset});
  const {items, total, limit} = await response.json();
  if (sequence !== projectListRequest) return;
  if (!items.length && projectPageOffset) {
    projectPageOffset = Math.max(0, projectPageOffset - limit);
    return refreshProjects();
  }
  const list = field('project-list');
  list.replaceChildren();
  for (const item of items) {
    const card = document.createElement('article');
    card.className = 'project-card';
    card.classList.toggle('is-current', item.id === openProject?.id);
    const open = document.createElement(trash ? 'div' : 'button');
    open.className = 'project-card-open';
    if (!trash) {
      open.type = 'button';
      open.setAttribute('aria-label', `打开项目「${item.label}」`);
    }
    const top = document.createElement('span');
    top.className = 'project-card-top';
    const icon = document.createElement('span');
    icon.className = 'project-card-icon';
    icon.append(uiIcon('folder'));
    top.append(icon);
    if (item.id === openProject?.id) {
      const current = document.createElement('span');
      current.className = 'project-card-current';
      current.textContent = '当前项目';
      top.append(current);
    }
    const title = document.createElement('strong');
    title.className = 'project-card-title';
    title.textContent = item.label;
    const footer = document.createElement('span');
    footer.className = 'project-card-footer';
    const date = document.createElement('span');
    const updated = item.updated ? new Date(item.updated) : null;
    date.textContent = updated && !Number.isNaN(updated.valueOf())
      ? `${trash ? '删除于' : '更新于'} ${new Intl.DateTimeFormat('zh-CN', {year: 'numeric', month: 'short', day: 'numeric'}).format(updated)}`
      : '项目工作区';
    footer.append(date);
    if (!trash) {
      footer.append(uiIcon('forward'));
      open.addEventListener('click', () => {
        if (!allowProjectSwitch()) return;
        run(open, '正在写入并打开项目…', async () => {
          await flushProjectWrite();
          await loadProject(item.id);
        });
      });
    }
    open.append(top, title, footer);
    const actions = document.createElement('div');
    actions.className = 'project-card-actions';
    const change = document.createElement('button');
    change.type = 'button';
    change.textContent = trash ? '恢复项目' : '删除项目';
    change.setAttribute('aria-label', `${change.textContent}「${item.label}」`);
    change.onclick = () => {
      if (!trash && !confirm(`将「${item.label}」移入回收站？项目文件和历史可以恢复。`)) return;
      run(change, trash ? '正在恢复项目…' : '正在移入回收站…', async () => {
        if (!trash && openProject?.id === item.id) await flushProjectWrite();
        await request(`/api/workspace/project/${trash ? 'restore-deleted' : 'delete'}`, {id: item.id});
        if (!trash && openProject?.id === item.id) resetProjectEditor();
        await refreshProjects();
        (field('project-list').querySelector('button') || field('project-trash-toggle')).focus();
        message(trash ? '已恢复，可在项目库打开。' : '已移入回收站。');
      });
    };
    actions.append(change);
    card.append(open, actions);
    list.append(card);
  }
  list.hidden = !items.length;
  field('project-empty').hidden = Boolean(items.length);
  field('project-empty-title').textContent = trash ? '回收站为空' : '还没有项目';
  field('project-empty-detail').textContent = trash ? '删除的项目会显示在这里。'
    : '新建空白项目，或导入 TXT、Markdown 开始制作。';
  field('project-empty-detail').hidden = Boolean(items.length);
  field('project-count').textContent = `${total} 个项目`;
  field('project-pages').hidden = total <= limit;
  field('project-prev').disabled = projectPageOffset === 0;
  field('project-next').disabled = projectPageOffset + limit >= total;
}

field('project-trash-toggle').onclick = (event) => {
  showProjectTrash = !showProjectTrash;
  projectPageOffset = 0;
  event.currentTarget.textContent = showProjectTrash ? '返回项目库' : '回收站';
  event.currentTarget.setAttribute('aria-pressed', String(showProjectTrash));
  field('project-library-title').textContent = showProjectTrash ? '回收站' : '全部项目';
  field('project-create-open').hidden = showProjectTrash;
  refreshProjects().catch((error) => message(error.message, true));
};
field('project-prev').onclick = () => {
  projectPageOffset = Math.max(0, projectPageOffset - 100);
  refreshProjects().catch((error) => message(error.message, true));
};
field('project-next').onclick = () => {
  projectPageOffset += 100;
  refreshProjects().catch((error) => message(error.message, true));
};

async function refreshProjectHistory() {
  if (!openProject) {
    projectHistoryItems = [];
    updateWorkspaceStatus();
    return;
  }
  const scope = openProject.id;
  const response = await request('/api/workspace/project/history', {id: scope});
  const result = await response.json();
  if (scope !== openProject?.id) return;
  projectHistoryItems = result.items || [];
  const list = field('project-history-list');
  list.replaceChildren();
  projectHistoryItems.forEach((item, index) => {
    const row = document.createElement('li');
    const details = document.createElement('div');
    details.className = 'project-history-details';
    const title = document.createElement('strong');
    title.textContent = item.current ? `${item.title} · 当前版本` : item.title;
    const date = document.createElement('small');
    const updated = item.updated ? new Date(item.updated) : null;
    date.textContent = updated && !Number.isNaN(updated.valueOf())
      ? new Intl.DateTimeFormat('zh-CN', {year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}).format(updated)
      : `版本 ${projectHistoryItems.length - index}`;
    details.append(title, date);
    row.append(details);
    if (!item.current) {
      const restore = document.createElement('button');
      restore.type = 'button';
      restore.textContent = '恢复此版本';
      restore.addEventListener('click', () => run(restore, '正在恢复历史版本…', async () => {
        await flushProjectWrite();
        const id = openProject?.id;
        if (!id) throw new Error('项目已关闭。');
        await request('/api/workspace/project/restore', {id, revision: item.revision});
        field('project-history-dialog').close();
        await loadProject(id);
        message(`已恢复到「${item.title}」，并写入新版本。`);
      }));
      row.append(restore);
    }
    list.append(row);
  });
  updateWorkspaceStatus();
}

async function loadProject(id, revision, destination = pendingProjectView || 'projects') {
  if (!id) throw new Error('请选择要打开的项目。');
  clearTimeout(projectWriteTimer);
  const response = await request('/api/workspace/project/open', {id, revision});
  const project = await response.json();
  openProject = {id: project.id, revision: project.revision};
  pendingProjectView = '';
  projectAddress(project.id);
  projectHistoryItems = [];
  field('project-name').value = project.title;
  applyProjectConfig(project.config);
  field('project-save-copy').disabled = false;
  raw.value = project.raw;
  scenario.value = '';
  cues.value = JSON.stringify(project.cues, null, 2);
  field('heading').value = project.heading;
  field('speakers').value = JSON.stringify(project.speakers, null, 2);
  uploads.clear();
  for (const [name, assetId] of Object.entries(project.assets)) useManagedMedia(name, assetId);
  invalidateScenes();
  renderFiles();
  font.value = project.font;
  if (project.scenario) showScenario(project.scenario);
  editorBaseline = editorFingerprint();
  updateProjectScope();
  await refreshProjects();
  await refreshProjectHistory();
  goWorkspace(destination);
  message(`已打开「${project.title}」。`);
}

async function saveProject(copy = false, title = field('project-name').value.trim()) {
  const scope = openProject;
  const fingerprint = editorFingerprint();
  const assets = Object.fromEntries([...uploads].map(([name, file]) => [name, managedAssets.get(file)]));
  const data = {
    ...(copy ? {} : openProject), title, raw: raw.value, scenario: scenarioData(),
    cues: mapping(cues.value, '演出 JSON'), assets, font: font.value, config: projectConfig(), heading: field('heading').value,
    speakers: mapping(field('speakers').value, '说话人映射'),
  };
  const response = await request('/api/workspace/project/save', data);
  const project = await response.json();
  if (scope !== openProject) return;
  const unchanged = fingerprint === editorFingerprint();
  openProject = {id: project.id, revision: project.revision};
  projectAddress(project.id);
  if (unchanged) field('project-name').value = project.title;
  for (const [name, id] of Object.entries(project.assets || {})) {
    const file = uploads.get(name);
    if (file) managedAssets.set(file, id);
  }
  field('project-save-copy').disabled = false;
  if (unchanged) editorBaseline = editorFingerprint();
  updateProjectScope();
  await refreshProjects();
  await refreshProjectHistory();
  message(`已写入「${project.title}」的历史版本。`);
}

field('project-save-copy').onclick = (event) => {
  const suffix = '（副本）';
  const name = field('project-new-name').value.trim() || field('project-name').value.trim().slice(0, 128 - suffix.length) + suffix;
  run(event.currentTarget, '正在写入并另存项目…', async () => {
    await flushProjectWrite();
    await saveProject(true, name);
  });
};
field('project-create-open').onclick = () => {
  field('project-create-dialog').showModal();
  field('project-new-name').focus();
};
field('project-create-cancel').onclick = () => field('project-create-dialog').close();
field('project-create-cancel-secondary').onclick = () => field('project-create-dialog').close();
field('project-create-dialog').addEventListener('click', (event) => {
  if (event.target === field('project-create-dialog')) field('project-create-dialog').close();
});
field('project-create').onsubmit = (event) => {
  event.preventDefault();
  if (!allowProjectSwitch()) return;
  const name = field('project-new-name').value.trim();
  if (!name) return message('请输入项目名称。', true);
  run(field('project-new'), '正在创建项目…', async () => {
    await flushProjectWrite();
    const file = field('project-raw-file').files[0];
    const rawText = file ? await readText(file) : '';
    const response = await request('/api/workspace/project/save', {
      title: name, raw: rawText, cues: {}, assets: {}, font: '', heading: '', speakers: {},
    });
    const project = await response.json();
    field('project-new-name').value = '';
    field('project-raw-file').value = '';
    field('project-raw-file-name').textContent = '暂未选择文件';
    field('project-create-dialog').close();
    await loadProject(project.id);
  });
};
field('project-keep').onclick = (event) => {
  const name = field('project-new-name').value.trim();
  if (!name) {
    field('project-new-name').focus();
    return message('请输入项目名称。', true);
  }
  run(event.currentTarget, '正在创建项目…', async () => {
    await saveProject(true, name);
    field('project-new-name').value = '';
    field('project-create-dialog').close();
  });
};
function resetProjectEditor() {
  openProject = null;
  pendingProjectView = '';
  projectAddress();
  projectHistoryItems = [];
  field('project-name').value = '';
  field('project-save-copy').disabled = true;
  field('heading').value = '';
  field('speakers').value = '{}';
  field('project-width').value = 1280;
  applyProjectConfig();
  font.value = '';
  raw.value = '';
  scenario.value = '';
  cues.value = '{}';
  uploads.clear();
  invalidateScenes();
  renderFiles();
  editorBaseline = editorFingerprint();
  updateProjectScope();
  goWorkspace('project-manager');
}
field('project-close').onclick = () => {
  if (!allowProjectSwitch()) return;
  run(field('project-close'), '正在写入并关闭项目…', async () => {
    await flushProjectWrite();
    resetProjectEditor();
    await refreshProjects();
    message('已关闭项目。');
  });
};
field('project-undo').onclick = (event) => run(event.currentTarget, '正在撤回到上一版本…', async () => {
  await flushProjectWrite();
  const id = openProject?.id;
  if (!id) throw new Error('项目已关闭。');
  await request('/api/workspace/project/undo', {id});
  await loadProject(id);
  message('已撤回到上一历史版本，并保留本次撤回记录。');
});
field('project-write').onclick = (event) => run(event.currentTarget, '正在写入项目…', async () => {
  await flushProjectWrite();
  message('修改已写入项目历史。');
});
field('project-history-open').onclick = async (event) => run(event.currentTarget, '正在读取项目历史…', async () => {
  await flushProjectWrite();
  await refreshProjectHistory();
  field('project-history-dialog').showModal();
});
field('project-history-close').onclick = () => field('project-history-dialog').close();
field('project-history-dialog').addEventListener('click', (event) => {
  if (event.target === field('project-history-dialog')) field('project-history-dialog').close();
});
field('project-name').addEventListener('input', updateProjectScope);
field('config-llm-scenario-enabled').addEventListener('change', () => {
  if (field('config-llm-scenario-enabled').checked && !scenarioLlmOverrideInitialized) {
    copyGlobalLlmToScenarioOverride();
    scenarioLlmOverrideInitialized = true;
  }
  updateScenarioLlmOverrideUi();
  refreshApiProviders();
  updateWorkspaceStatus();
});
for (const id of ['heading', 'speakers', ...projectConfigIds]) field(id).addEventListener('input', updateWorkspaceStatus);
for (const id of projectConfigIds) field(id).addEventListener('change', updateWorkspaceStatus);
refreshProjects().catch((error) => {
  field('project-empty-title').textContent = '项目列表读取失败';
  const detail = field('project-empty-detail');
  detail.textContent = '请确认 Prisma 服务正在运行，然后刷新此页。';
  detail.hidden = false;
  field('project-count').textContent = '读取失败';
  message(error.message, true);
});
action.addEventListener('change', refreshCueForm);
font.addEventListener('change', () => { exported = false; updateWorkspaceStatus(); });

field('cue-form').addEventListener('submit', (event) => {
  event.preventDefault();
  try {
    if (!selectedLine || inspectedText !== scenario.value) throw new Error('请先解析并选择一条文本。');
    const kind = action.value;
    const actor = field('actor').value.trim();
    const file = asset.value;
    if (!asset.hidden && !file) throw new Error('请选择一个素材文件。');
    if ((kind === 'show' || kind === 'hide') && !actor) throw new Error('请填写角色名。');
    if (kind === 'voice' && (!selectedLine.speaker || selectedLine.speaker === '？')) {
      throw new Error('请为已确认说话人的对白添加配音。');
    }
    let cue = {[kind]: file};
    if (kind === 'show') cue = {show: {actor, file}};
    if (kind === 'hide') cue = {hide: actor};
    if (kind === 'stop') cue = {music: null};
    const plan = mapping(cues.value, '演出 JSON');
    const {scene, index, id} = selectedLine;
    const positional = plan[scene]?.[index] !== undefined;
    if (positional && id && plan[lineCues] && Object.hasOwn(plan[lineCues], id)) {
      throw new Error('该条目同时用了编号和 ID，请保留一组演出动作。');
    }
    const useId = id && !positional;
    const group = (plan[useId ? lineCues : scene] ??= {});
    if (typeof group !== 'object' || Array.isArray(group)) throw new Error('演出绑定须为 JSON 对象。');
    const key = useId ? id : index;
    if (!Object.hasOwn(group, key)) group[key] = [];
    const commands = group[key];
    if (!Array.isArray(commands)) throw new Error('该条目的演出动作需要一个数组。');
    if (kind === 'voice' && commands.some((command) => command && 'voice' in Object(command))) {
      throw new Error('这句已绑定配音，可在演出 JSON 中修改。');
    }
    commands.push(cue);
    cues.value = JSON.stringify(plan, null, 2);
    cues.dispatchEvent(new Event('input'));
    exported = false;
    updateWorkspaceStatus();
    message(`已为 ${selectedLine.scene}/${selectedLine.index} 添加演出动作。`);
  } catch (error) {
    message(error.message, true);
  }
});

function encode(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',', 2)[1]);
    reader.onerror = () => reject(new Error(`无法读取 ${file.name}。`));
    reader.readAsDataURL(file);
  });
}

field('build').addEventListener('click', (event) => run(event.currentTarget, '正在构建 Ren’Py 工程…', async () => {
  const project = openProject?.id;
  const fingerprint = editorFingerprint();
  exported = false;
  updateWorkspaceStatus();
  const scriptId = nodeInputs.find((item) => item.kind === 'script')?.id;
  let payload;
  if (scriptId) {
    payload = {script_id: scriptId, project_id: project};
  } else {
    if (location.hash === '#stage-export') throw new Error('请先通过脚本版本。');
    payload = scriptInputs();
    if (!payload.scenario) throw new Error('请先生成或导入 Scenario。');
  }
  const response = await request('/api/build', payload);
  download(await response.blob(), 'prisma-renpy.zip');
  if (project !== openProject?.id) return;
  const id = response.headers.get('X-Prisma-Export-Id');
  if (id) reportNodeOutput('export', {reference: {kind: 'export', id}, label: 'Ren’Py 工程 ZIP',
    fingerprint});
  exported = true;
  updateWorkspaceStatus();
  message('Ren’Py 工程已开始下载。');
}));

enablePipelinePanning();
pipeline.addEventListener('click', (event) => {
  const node = event.target.closest('.flow-node');
  if (!node) return;
  goWorkspace(`stage-${node.dataset.node}`);
  if (openProject) field('pipeline-stage').focus({preventScroll: true});
});
new MutationObserver(updateWorkspaceStatus).observe(field('image-gallery'), {childList: true});

renderFiles();
editorBaseline = editorFingerprint();
updateWorkspaceStatus();
const linkedProject = new URLSearchParams(location.search).get('project');
const linkedWorkspace = location.hash.slice(1) || 'projects';
workspaceView();
if (linkedProject) document.addEventListener('DOMContentLoaded', () => {
  loadProject(linkedProject, undefined, linkedWorkspace).catch((error) => {
    if (!openProject) projectAddress();
    message(error.message, true);
  });
}, {once: true});
