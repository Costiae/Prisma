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
let openProject = null;
let selectedPipelineNode = null;
let exported = false;
const imageExtensions = /\.(png|jpe?g|webp)$/i;
const audioExtensions = /\.(ogg|opus|mp3|wav|flac)$/i;
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

function message(text, isError = false) {
  status.textContent = text;
  status.classList.toggle('error', isError);
  status.setAttribute('role', isError ? 'alert' : 'status');
}

function updatePipeline() {
  const rawLength = raw.value.trim().length;
  const fresh = Boolean(scenario.value.trim() && inspectedText === scenario.value);
  const lineCount = scenes.reduce((total, scene) => total + scene.lines.length, 0);
  const speakerCount = new Set(scenes.flatMap((scene) => scene.lines.map((line) => line.speaker).filter(Boolean))).size;
  const unresolved = scenes.reduce((total, scene) => total + scene.lines.filter((line) => line.speaker === '？').length, 0);
  let cueCount = 0;
  const references = new Set();
  let cueError = false;
  try {
    const plan = mapping(cues.value, '演出 JSON');
    for (const scene of Object.values(plan)) {
      if (!scene || typeof scene !== 'object' || Array.isArray(scene)) continue;
      for (const commands of Object.values(scene)) {
        if (!Array.isArray(commands)) continue;
        for (const command of commands) {
          if (!command || typeof command !== 'object' || Array.isArray(command)) continue;
          cueCount += 1;
          for (const [kind, value] of Object.entries(command)) {
            const name = kind === 'show' ? value?.file : value;
            if (['background', 'show', 'music', 'sound', 'voice'].includes(kind) && typeof name === 'string') references.add(name);
          }
        }
      }
    }
  } catch {
    cueError = true;
  }
  const mediaCount = uploads.size;
  const voiceCount = [...uploads.keys()].filter((name) => audioExtensions.test(name)).length;
  const candidateCount = field('image-gallery')?.querySelectorAll('figure').length ?? 0;
  const set = (id, value) => {
    const node = field(id);
    if (node) node.textContent = value;
    document.querySelectorAll(`[data-flow-id="${id}"]`).forEach((item) => { item.textContent = value; });
  };

  set('flow-raw-state', rawLength ? '已载入' : '等待原文');
  set('flow-raw-value', rawLength ? `${rawLength.toLocaleString()} 字 · 另带起始章节与说话人映射` : '原文 + 章节起点 + 说话人映射');
  set('flow-scenario-state', fresh ? `${scenes.length} 场已解析` : scenario.value.trim() ? '待解析 / 有修改' : '等待生成');
  set('flow-scenario-value', fresh ? `${scenes.length} 场 · ${lineCount} 条台词 · ${speakerCount} 个说话人` : '章节 → 场景 ID → 有序台词与说话人');
  set('flow-script-state', cueError ? '演出 JSON 有误' : fresh ? `${cueCount} 个动作` : '等待解析');
  set('flow-script-value', fresh ? `${cueCount} 个动作 · ${references.size} 个文件引用 · 已添加 ${mediaCount} 个文件` : 'Scenario + cue JSON + 实际引用文件');
  set('flow-export-state', exported ? '已生成并下载' : fresh ? '可构建' : '尚未导出');
  set('flow-background-state', candidateCount ? `${candidateCount} 张候选` : '候选可生成');
  set('flow-sprite-state', candidateCount ? `${candidateCount} 张候选` : '可生成候选');
  set('flow-cg-state', candidateCount ? `${candidateCount} 张候选` : '可生成候选');
  set('flow-voice-state', voiceCount ? `${voiceCount} 个音频文件` : '可手动挂接');

  const selected = selectedPipelineNode && document.querySelector(`.flow-node[data-node="${selectedPipelineNode}"]`);
  if (!selected) return;
  const states = {
    raw: rawLength ? '已载入' : '等待原文',
    scenario: fresh ? `${scenes.length} 场已解析` : scenario.value.trim() ? '待解析 / 有修改' : '等待生成',
    script: cueError ? '演出 JSON 有误' : fresh ? `${cueCount} 个动作` : '等待解析',
    'background-assets': candidateCount ? `${candidateCount} 张候选图` : '候选可生成',
    'sprite-assets': candidateCount ? `${candidateCount} 张候选图` : '可生成候选',
    'cg-assets': candidateCount ? `${candidateCount} 张候选图` : '可生成候选',
    'voice-assets': voiceCount ? `${voiceCount} 个音频文件` : '可手动挂接',
    'music-reference': '参考输入',
    'character-reference': '参考输入',
    'voice-reference': '参考输入',
  };
  set('flow-detail-title', selected.dataset.title);
  const detailState = field('flow-detail-state');
  detailState.textContent = states[selectedPipelineNode] || selected.querySelector('.node-state')?.textContent || '规划中';
  detailState.className = `node-state${selected.classList.contains('is-plan') ? ' is-plan' : selected.classList.contains('is-partial') ? ' is-partial' : selected.classList.contains('is-reference') ? ' is-reference' : ''}`;
  set('flow-detail-input', selected.dataset.input || '—');
  set('flow-detail-handoff', selected.dataset.handoff || '—');
  set('flow-detail-output', selected.dataset.output || '—');
  const current = {
    raw: rawLength ? `当前输入：${rawLength.toLocaleString()} 字。` : '当前输入：尚未载入原文。',
    scenario: fresh ? `当前输出：${scenes.length} 场、${lineCount} 条台词、${speakerCount} 个说话人；${unresolved} 条说话人待确认。` : scenario.value.trim() ? '当前输出：Scenario 已有内容，解析结果未生成或已过期。' : '当前输出：尚未生成 Scenario。',
    script: cueError ? '当前状态：演出 JSON 解析失败，无法构建。' : `当前传递：${cueCount} 个演出动作、${references.size} 个文件引用；素材区已有 ${mediaCount} 个文件。${[...references].some((name) => !uploads.has(name)) ? '有引用文件尚未加入。' : ''}`,
    'background-assets': `当前传递：图像工作室有 ${candidateCount} 张候选；需要手动加入素材，再在演出中绑定。`,
    'sprite-assets': `当前传递：图像工作室有 ${candidateCount} 张候选；立绘差分尚未自动继承角色基准立绘。`,
    'cg-assets': `当前传递：图像工作室有 ${candidateCount} 张候选；CG 尚未自动继承角色基准立绘。`,
    'voice-assets': `当前传递：素材区有 ${voiceCount} 个音频文件，Scenario 与 cue 编辑器可手动绑定已录制的语音。`,
    'music-reference': '当前状态：拓扑定义了音乐参考入口；参考素材的导入与归档控件尚未接入。',
    'character-reference': '当前状态：拓扑定义了角色参考入口；参考素材的导入与归档控件尚未接入。',
    'voice-reference': '当前状态：拓扑定义了人声参考入口；参考素材的导入与归档控件尚未接入。',
  };
  set('flow-detail-current', current[selectedPipelineNode] || '该分支目前标记为规划中，输入、交接字段和下游用途按制作关系表展示。');
  const link = field('flow-detail-link');
  link.hidden = !selected.dataset.target;
  if (selected.dataset.target) link.href = selected.dataset.target;
}

document.querySelectorAll('.flow-node').forEach((node) => node.addEventListener('click', () => {
  document.querySelectorAll('.flow-node[aria-pressed="true"]').forEach((active) => active.setAttribute('aria-pressed', 'false'));
  selectedPipelineNode = node.dataset.node;
  document.querySelectorAll(`.flow-node[data-node="${selectedPipelineNode}"]`).forEach((match) => match.setAttribute('aria-pressed', 'true'));
  document.querySelectorAll('.pipeline-edge').forEach((edge) => {
    edge.classList.toggle('is-related', edge.dataset.from === selectedPipelineNode || edge.dataset.to === selectedPipelineNode);
  });
  updatePipeline();
}));
document.querySelectorAll('.route-step[data-target]').forEach((step) => step.addEventListener('click', () => {
  document.querySelector(step.dataset.target)?.scrollIntoView({behavior: 'smooth', block: 'start'});
}));
document.querySelectorAll('#flow-detail-link, .pipeline-heading .section-link').forEach((link) => link.addEventListener('click', (event) => {
  event.preventDefault();
  document.querySelector(link.getAttribute('href'))?.scrollIntoView({behavior: 'smooth', block: 'start'});
}));
if (field('image-gallery')) new MutationObserver(updatePipeline).observe(field('image-gallery'), {childList: true, subtree: true});

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

async function run(button, pending, task) {
  button.disabled = true;
  message(pending);
  try {
    await task();
  } catch (error) {
    message(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderLines() {
  const list = field('lines');
  list.replaceChildren();
  const scene = scenes[Number(sceneSelect.value)];
  if (!scene) return;
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
      refreshCueForm();
      updatePipeline();
    });
    list.append(row);
  });
  list.scrollTop = 0;
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
  updatePipeline();
}

async function inspectScenario(text = scenario.value) {
  const response = await request('/api/scenes', {scenario: text});
  const data = await response.json();
  if (scenario.value !== text) return;
  scenes = data.scenes;
  inspectedText = text;
  selectedLine = null;
  sceneSelect.replaceChildren(...scenes.map((scene, index) => new Option(`${scene.id} · ${scene.chapter}`, index)));
  sceneSelect.disabled = false;
  renderLines();
  refreshCueForm();
  const pending = scenes.reduce((total, scene) => total + scene.lines.filter((line) => line.speaker === '？').length, 0);
  field('scene-summary').textContent = `${scenes.length} 场 · ${pending} 条待校对`;
  message(pending ? `已解析 ${scenes.length} 场。请在 Scenario 中校对 ${pending} 条对白。` : `已解析 ${scenes.length} 场，可以安排演出或直接导出。`);
  updatePipeline();
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
      if (editor === cues) mapping(content, '演出 JSON');
      editor.value = content;
      if (editor === scenario) { exported = false; invalidateScenes(); }
      if (editor === cues) exported = false;
      updatePipeline();
      message(`已导入 ${file.name}。`);
    } catch (error) {
      message(`无法导入 ${file.name}。${error.message}`, true);
    }
  });
}

raw.addEventListener('input', () => { exported = false; updatePipeline(); });
field('sample').addEventListener('click', () => {
  if (raw.value.trim() && !confirm('替换原文和说话人映射？')) return;
  raw.value = sample;
  field('heading').value = '';
  field('speakers').value = '{"6": "林岚", "7": "周明"}';
  exported = false;
  updatePipeline();
  message('已载入车站样例，点击「生成 Scenario」继续。');
});

field('draft').addEventListener('click', (event) => {
  if (scenario.value.trim() && !confirm('用新草稿替换当前 Scenario？')) return;
  exported = false;
  run(event.currentTarget, '正在生成 Scenario…', async () => {
    const previous = scenario.value;
    const response = await request('/api/draft', {
      raw: raw.value,
      heading: field('heading').value,
      speakers: mapping(field('speakers').value, '说话人映射'),
      line_ids: true,
    });
    const result = await response.json();
    if (scenario.value !== previous) throw new Error('生成期间 Scenario 已编辑，请重新生成或先下载当前稿。');
    scenario.value = result.scenario;
    invalidateScenes();
    await inspectScenario(result.scenario);
  });
});

scenario.addEventListener('input', () => { exported = false; invalidateScenes(); });
cues.addEventListener('input', () => { exported = false; updatePipeline(); });
field('inspect').addEventListener('click', (event) => run(event.currentTarget, '正在解析场景…', () => inspectScenario()));
field('anchor-scenario').addEventListener('click', (event) => run(event.currentTarget, '正在固定演出位置…', async () => {
  const text = scenario.value;
  const plan = cues.value;
  const response = await request('/api/scenario/anchor', {scenario: text, cues: plan});
  const result = await response.json();
  if (scenario.value !== text || cues.value !== plan) throw new Error('固定期间剧本或演出已编辑，请重试。');
  scenario.value = result.scenario;
  cues.value = JSON.stringify(result.cues, null, 2);
  exported = false;
  invalidateScenes();
  await inspectScenario(result.scenario);
  if (inspectedText === result.scenario) message('已固定演出位置。请一同保存 Scenario 和演出 JSON。');
}));
field('save-scenario').addEventListener('click', (event) => {
  if (!scenario.value.trim()) return message('请先填写 Scenario。', true);
  run(event.currentTarget, '正在保存 Scenario…', async () => {
    const text = scenario.value;
    await request('/api/scenario/save', {raw: raw.value, scenario: text});
    download(new Blob([text], {type: 'text/markdown;charset=utf-8'}), 'scenario.md');
    message('Scenario 已登记并下载。');
  });
});
field('save-cues').addEventListener('click', (event) => run(event.currentTarget, '正在保存演出绑定…', async () => {
  const text = cues.value.trim() || '{}';
  await request('/api/scenario/save', {scenario: scenario.value, cues: mapping(text, '演出 JSON')});
  download(new Blob([text], {type: 'application/json'}), 'cues.json');
  message('演出绑定已登记并下载。');
}));
sceneSelect.addEventListener('change', () => {
  selectedLine = null;
  renderLines();
  refreshCueForm();
  updatePipeline();
});

function renderFiles() {
  exported = false;
  const list = field('file-list');
  list.replaceChildren();
  const selectedFont = font.value;
  font.replaceChildren(new Option('Ren’Py 默认', ''));
  for (const name of uploads.keys()) {
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = `${name} ×`;
    remove.setAttribute('aria-label', `移除 ${name}`);
    remove.addEventListener('click', () => {
      uploads.delete(name);
      renderFiles();
    });
    list.append(remove);
    if (fontExtensions.test(name)) font.add(new Option(name, name));
  }
  if (!uploads.size) list.textContent = '可添加图片、音频和字体。素材文件按名称引用。';
  if (uploads.has(selectedFont)) font.value = selectedFont;
  refreshCueForm();
  updatePipeline();
}

async function registerMedia(name, data) {
  const response = await request('/api/workspace/assets/import', {name, ...data});
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
  event.target.disabled = true;
  try {
    for (const file of files) {
      if (file.size > 45 * 1024 * 1024) throw new Error(`素材「${file.name}」超过 45 MiB。`);
      const resource = await registerMedia(file.name, {data: await encode(file)});
      useManagedMedia(file.name, resource.id, file);
    }
    if (files.length) message('素材已登记到资源目录。');
  } catch (error) { message(error.message, true); }
  finally { renderFiles(); event.target.disabled = false; }
});

async function refreshProjects() {
  const response = await request('/api/images/library/list', {kind: 'project', limit: 100});
  const {items} = await response.json();
  const select = field('project-select');
  const selected = openProject?.id || select.value;
  select.replaceChildren(new Option('选择项目', ''), ...items.map((item) => new Option(item.label, item.id)));
  select.value = selected;
}

async function loadProject(id, revision) {
  if (!id) throw new Error('请选择要打开的项目。');
  const response = await request('/api/workspace/project/open', {id, revision});
  const project = await response.json();
  openProject = {id: project.id, revision: project.revision};
  field('project-name').value = project.title;
  field('project-save-copy').disabled = false;
  raw.value = project.raw;
  scenario.value = project.scenario;
  cues.value = JSON.stringify(project.cues, null, 2);
  field('heading').value = project.heading;
  field('speakers').value = JSON.stringify(project.speakers, null, 2);
  uploads.clear();
  for (const [name, assetId] of Object.entries(project.assets)) useManagedMedia(name, assetId);
  invalidateScenes();
  renderFiles();
  font.value = project.font;
  await refreshProjects();
  message(`已打开「${project.title}」。`);
}

async function saveProject(copy = false) {
  const title = field('project-name').value.trim();
  const assets = Object.fromEntries([...uploads].map(([name, file]) => [name, managedAssets.get(file)]));
  const data = {
    ...(copy ? {} : openProject), title, raw: raw.value, scenario: scenario.value,
    cues: mapping(cues.value, '演出 JSON'), assets, font: font.value, heading: field('heading').value,
    speakers: mapping(field('speakers').value, '说话人映射'),
  };
  const response = await request('/api/workspace/project/save', data);
  const project = await response.json();
  openProject = {id: project.id, revision: project.revision};
  field('project-save-copy').disabled = false;
  await refreshProjects();
  message(`已保存「${project.title}」。`);
}

field('project-save').onclick = (event) => run(event.currentTarget, '正在保存项目…', () => saveProject());
field('project-save-copy').onclick = (event) => run(event.currentTarget, '正在另存项目…', () => saveProject(true));
field('project-open').onclick = (event) => run(event.currentTarget, '正在读取项目…',
  () => loadProject(field('project-select').value));
refreshProjects().catch((error) => message(error.message, true));
action.addEventListener('change', refreshCueForm);
font.addEventListener('change', () => { exported = false; updatePipeline(); });

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
    exported = false;
    updatePipeline();
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
  exported = false;
  updatePipeline();
  const text = scenario.value;
  const planText = cues.value;
  const plan = mapping(planText, '演出 JSON');
  const chosenFont = font.value;
  const referenced = new Set(chosenFont ? [chosenFont] : []);
  for (const scene of Object.values(plan)) {
    if (!scene || typeof scene !== 'object' || Array.isArray(scene)) throw new Error('每组演出绑定须为 JSON 对象。');
    for (const commands of Object.values(scene)) {
      if (!Array.isArray(commands)) throw new Error('每个文本条目的演出动作须为数组。');
      for (const cue of commands) {
        if (!cue || typeof cue !== 'object' || Array.isArray(cue)) throw new Error('每个演出动作须为对象。');
        for (const [kind, value] of Object.entries(cue)) {
          const name = kind === 'show' ? value?.file : value;
          if (['background', 'show', 'music', 'sound', 'voice'].includes(kind) && typeof name === 'string') {
            referenced.add(name);
          }
        }
      }
    }
  }
  const assetIds = {};
  for (const name of referenced) {
    const file = uploads.get(name);
    if (!file) throw new Error(`请添加素材文件「${name}」。`);
    assetIds[name] = managedAssets.get(file);
    if (!assetIds[name]) throw new Error(`素材「${name}」尚未登记。`);
  }
  const response = await request('/api/build', {
    scenario: text, cues: planText, font: chosenFont, asset_ids: assetIds, project_id: openProject?.id,
  });
  download(await response.blob(), 'prisma-renpy.zip');
  exported = true;
  updatePipeline();
  message('Ren’Py 工程已开始下载。');
}));

refreshCueForm();
selectedPipelineNode = 'scenario';
document.querySelectorAll('.flow-node[data-node="scenario"]').forEach((node) => node.setAttribute('aria-pressed', 'true'));
document.querySelectorAll('.pipeline-edge').forEach((edge) => {
  edge.classList.toggle('is-related', edge.dataset.from === selectedPipelineNode || edge.dataset.to === selectedPipelineNode);
});
updatePipeline();
