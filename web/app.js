const field = (id) => document.getElementById(id);
const raw = field('raw');
const scenario = field('scenario');
const status = field('status');
const sceneSelect = field('scene');
const action = field('action');
const asset = field('asset');
const font = field('font');
const cues = field('cues');
const uploads = new Map();
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
      selectedLine = {scene: scene.id, index: index + 1, speaker: line.speaker};
      if (line.speaker && line.speaker !== '？') field('actor').value = line.speaker;
      refreshCueForm();
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
      if (editor === scenario) invalidateScenes();
      message(`已导入 ${file.name}。`);
    } catch (error) {
      message(`无法导入 ${file.name}。${error.message}`, true);
    }
  });
}

field('sample').addEventListener('click', () => {
  if (raw.value.trim() && !confirm('替换原文和说话人映射？')) return;
  raw.value = sample;
  field('heading').value = '';
  field('speakers').value = '{"6": "林岚", "7": "周明"}';
  message('已载入车站样例，点击「生成 Scenario」继续。');
});

field('draft').addEventListener('click', (event) => {
  if (scenario.value.trim() && !confirm('用新草稿替换当前 Scenario？')) return;
  run(event.currentTarget, '正在生成 Scenario…', async () => {
    const previous = scenario.value;
    const response = await request('/api/draft', {
      raw: raw.value,
      heading: field('heading').value,
      speakers: mapping(field('speakers').value, '说话人映射'),
    });
    const result = await response.json();
    if (scenario.value !== previous) throw new Error('生成期间 Scenario 已编辑，请重新生成或先下载当前稿。');
    scenario.value = result.scenario;
    invalidateScenes();
    await inspectScenario(result.scenario);
  });
});

scenario.addEventListener('input', invalidateScenes);
field('inspect').addEventListener('click', (event) => run(event.currentTarget, '正在解析场景…', () => inspectScenario()));
field('save-scenario').addEventListener('click', () => {
  if (!scenario.value.trim()) return message('请先填写 Scenario。', true);
  download(new Blob([scenario.value], {type: 'text/markdown;charset=utf-8'}), 'scenario.md');
});
field('save-cues').addEventListener('click', () => {
  try {
    mapping(cues.value, '演出 JSON');
    download(new Blob([cues.value.trim() || '{}'], {type: 'application/json'}), 'cues.json');
  } catch (error) {
    message(error.message, true);
  }
});
sceneSelect.addEventListener('change', () => {
  selectedLine = null;
  renderLines();
  refreshCueForm();
});

function renderFiles() {
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
}

field('files').addEventListener('change', (event) => {
  const files = [...event.target.files];
  event.target.value = '';
  const duplicate = files.find((file) => uploads.has(file.name));
  if (duplicate && !confirm(`替换已添加的同名素材，例如 ${duplicate.name}？`)) return;
  for (const file of files) uploads.set(file.name, file);
  renderFiles();
});
action.addEventListener('change', refreshCueForm);

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
    const scene = (plan[selectedLine.scene] ??= {});
    if (typeof scene !== 'object' || Array.isArray(scene)) throw new Error('该场景须包含按条目编号的动作。');
    const commands = (scene[selectedLine.index] ??= []);
    if (!Array.isArray(commands)) throw new Error('该条目的演出动作需要一个数组。');
    if (kind === 'voice' && commands.some((command) => command && 'voice' in Object(command))) {
      throw new Error('这句已绑定配音，可在演出 JSON 中修改。');
    }
    commands.push(cue);
    cues.value = JSON.stringify(plan, null, 2);
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
  const text = scenario.value;
  const planText = cues.value;
  const plan = mapping(planText, '演出 JSON');
  const chosenFont = font.value;
  const referenced = new Set(chosenFont ? [chosenFont] : []);
  for (const scene of Object.values(plan)) {
    if (!scene || typeof scene !== 'object' || Array.isArray(scene)) throw new Error('每个场景须包含按条目编号的动作。');
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
  const files = [];
  for (const name of referenced) {
    const file = uploads.get(name);
    if (!file) throw new Error(`请添加素材文件「${name}」。`);
    files.push(file);
  }
  if (files.reduce((total, file) => total + file.size, 0) > 45 * 1024 * 1024) {
    throw new Error('选中的素材超过 45 MiB，请减少素材或使用命令行构建。');
  }
  const encoded = Object.fromEntries(await Promise.all(files.map(async (file) => [file.name, await encode(file)])));
  const response = await request('/api/build', {scenario: text, cues: planText, font: chosenFont, files: encoded});
  download(await response.blob(), 'prisma-renpy.zip');
  message('Ren’Py 工程已开始下载。');
}));

refreshCueForm();
