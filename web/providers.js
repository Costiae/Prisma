const providerKinds = {text: '文本', image: '图像', audio: '音频／语音'};
const providerFormats = {openai: 'OpenAI 兼容', anthropic: 'Anthropic 兼容'};
let providerCatalog = {providers: [], defaults: {}};
let providerCatalogRequest = 0;
const providerChecks = new Map();
const providerBusy = new Set();
let editingProvider;
let editingModelProvider;
let modelPage = 0;
const modelsPerPage = 40;

function providerRefValue(ref) {
  return ref?.provider_id && ref?.model ? JSON.stringify([ref.provider_id, ref.model]) : '';
}

function readProviderRef(value) {
  if (!value) return null;
  const [provider_id, model] = JSON.parse(value);
  return {provider_id, model};
}

function declaredOptions(kind, showModelId = false) {
  return providerCatalog.providers.flatMap((provider) => provider.models.filter((model) => model.kind === kind)
    .map((model) => new Option(`${provider.name} · ${showModelId ? model.id : model.name}`,
      providerRefValue({provider_id: provider.id, model: model.id}))));
}

function defaultModelLabel(kind) {
  const ref = providerCatalog.defaults[kind];
  const provider = providerCatalog.providers.find((item) => item.id === ref?.provider_id);
  const model = provider?.models.find((item) => item.id === ref.model);
  return model ? `工作区默认 · ${provider.name} / ${model.name}` : '工作区默认 · 未设置';
}

const nodeModelSelectors = {
  'scenario-run-model': 'text',
  'image-node-model': 'image',
  'voice-node-model': 'audio',
};

function nodeModelKey(select) {
  return openProject ? 'prisma.node-model.' + openProject.id + '.' + select.id : '';
}

function refreshNodeModelSelectors() {
  for (const [id, kind] of Object.entries(nodeModelSelectors)) {
    const select = field(id);
    const key = nodeModelKey(select);
    const selected = key ? sessionStorage.getItem(key) || '' : '';
    const choices = [new Option(defaultModelLabel(kind), ''), ...declaredOptions(kind, true)];
    if (selected && !choices.some((option) => option.value === selected)) {
      choices.push(new Option(readProviderRef(selected).model + ' · 已不可用', selected));
    }
    select.replaceChildren(...choices);
    select.value = selected;
  }
}

function effectiveNodeModel(id) {
  const kind = nodeModelSelectors[id];
  const ref = readProviderRef(field(id).value) || providerCatalog.defaults[kind];
  if (!ref) return null;
  const provider = providerCatalog.providers.find((item) => item.id === ref.provider_id);
  return provider?.models.some((item) => item.id === ref.model && item.kind === kind) ? ref : null;
}

function refreshProjectLlmStatus() {
  refreshNodeModelSelectors();
  field('online-default-model').textContent = defaultModelLabel('image');
  updateVoiceMode();
}

function providerMessage(output, message, error = false) {
  output.textContent = message;
  output.classList.toggle('error-text', error);
  output.hidden = !message;
}

function providerButton(text, handler) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = text;
  button.onclick = handler;
  return button;
}

const capabilityLabels = {
  reasoning: '思考', multimodal: '多模态', image_input: '图像输入', audio_input: '音频输入',
  video_input: '视频输入', tools: '工具调用', structured_output: '结构化输出',
};
const probeOperations = ['chat/completions', 'responses', 'completions', 'images/generations', 'audio/speech',
  'embeddings', 'moderations', 'audio/transcriptions', 'rerank', 'realtime/client_secrets'];
const runningModelTests = new Set();
const transientModelTests = new Map();
let selectedProviderId;

function providerNode(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content) node.textContent = content;
  return node;
}

function currentProvider() {
  return providerCatalog.providers.find((provider) => provider.id === selectedProviderId);
}

function selectProvider(id) {
  selectedProviderId = id;
  modelPage = 0;
  field('api-model-query').value = '';
  field('api-model-filter').value = 'all';
  renderProviders();
  [...field('api-provider-list').children].find((button) => button.dataset.provider === id)?.focus();
}

function renderProviders() {
  const host = field('api-provider-list');
  const provider = currentProvider() || providerCatalog.providers.find((item) => item.models.length) || providerCatalog.providers[0];
  selectedProviderId = provider?.id;
  field('api-provider-workspace').hidden = !provider;
  host.replaceChildren(...providerCatalog.providers.map((item) => {
    const choice = providerButton('', () => selectProvider(item.id));
    choice.className = 'provider-choice';
    choice.dataset.provider = item.id;
    choice.setAttribute('aria-pressed', String(item.id === selectedProviderId));
    choice.append(providerNode('strong', '', item.name),
      providerNode('span', '', providerFormats[item.protocol]), providerNode('span', 'provider-choice-count', String(item.models.length)));
    return choice;
  }));
  if (!provider) return;
  field('api-provider-active-name').textContent = provider.name;
  field('api-provider-active-url').textContent =
    providerFormats[provider.protocol] + ' · ' + provider.base_url + ' · ' + (provider.configured ? 'Key 已保存' : '无 Key');
  renderConnectionState();
  renderModelRows();
}

function renderConnectionState() {
  const check = providerChecks.get(selectedProviderId) || {};
  const output = field('api-provider-connection-state');
  const status = check.test;
  output.className = 'provider-connection-state';
  providerMessage(output, status?.message || '尚未检查模型目录接口', status?.error);
  providerMessage(field('api-provider-fetch-state'), check.models?.message || '', check.models?.error);
  field('api-provider-fetch').disabled = providerBusy.has(selectedProviderId + ':models');
  field('api-provider-test-connection').disabled = providerBusy.has(selectedProviderId + ':test');
}

function testKey(provider, model) {
  return providerRefValue({provider_id: provider.id, model: model.id});
}

function renderModelRows() {
  const provider = currentProvider();
  if (!provider) return;
  const host = field('api-model-list');
  const query = field('api-model-query').value.trim().toLowerCase();
  const kind = field('api-model-filter').value;
  const filtered = provider.models.filter((model) =>
    (kind === 'all' || kind === model.kind) && (model.id + ' ' + model.name).toLowerCase().includes(query));
  const total = provider.models.length;
  field('api-model-count').textContent = total ? (total === filtered.length ? total + ' 个模型'
    : '显示 ' + filtered.length + ' / ' + total + ' 个模型') : '尚未添加模型';
  field('api-model-filters').hidden = !total;
  if (!filtered.length) {
    host.replaceChildren(providerNode('p', 'provider-empty-models', total
      ? '没有匹配的模型。试试其他关键词或用途。'
      : '点击 Fetch Models 读取目录。服务商未提供目录时，使用「手动添加」。'));
    return;
  }

  const table = providerNode('div', 'provider-model-table');
  const tableHead = providerNode('div', 'provider-model-head');
  tableHead.setAttribute('aria-hidden', 'true');
  for (const title of ['模型', '能力', '最近一次模型测试', '操作']) tableHead.append(providerNode('span', '', title));
  const rows = providerNode('ul', 'provider-model-rows');
  const pages = Math.ceil(filtered.length / modelsPerPage);
  modelPage = Math.min(modelPage, pages - 1);
  const first = modelPage * modelsPerPage;
  for (const model of filtered.slice(first, first + modelsPerPage)) rows.append(renderModelRow(provider, model));
  table.append(tableHead, rows);
  if (pages > 1) {
    const navigation = providerNode('nav', 'provider-pagination');
    navigation.setAttribute('aria-label', '模型目录分页');
    const pageButton = (label, direction) => providerButton(label, () => {
      modelPage += direction;
      renderModelRows();
      field('api-model-query').focus({preventScroll: true});
    });
    const previous = pageButton('上一页', -1), next = pageButton('下一页', 1);
    previous.disabled = modelPage === 0;
    next.disabled = modelPage + 1 === pages;
    navigation.append(previous, providerNode('span', '', (modelPage + 1) + ' / ' + pages), next);
    table.append(navigation);
  }
  host.replaceChildren(table);
}

function renderModelRow(provider, model) {
  const row = providerNode('li', 'provider-model-row');
  const identity = providerNode('div', 'provider-model-identity');
  const title = providerNode('strong', '', model.name);
  const role = providerNode('div', 'provider-model-role');
  const ref = providerCatalog.defaults[model.kind];
  role.textContent = (providerKinds[model.kind] || '其他') +
    (ref?.provider_id === provider.id && ref.model === model.id ? ' · 工作区默认' : '');
  identity.append(title);
  if (model.name !== model.id) identity.append(providerNode('code', 'provider-model-id', model.id));
  identity.append(role);

  const capabilities = providerNode('div', 'provider-model-badges');
  const features = model.capabilities || {};
  for (const [name, label] of Object.entries(capabilityLabels)) {
    const value = features[name];
    if (name !== 'reasoning' && name !== 'multimodal' && value !== true) continue;
    const status = value === true ? '支持' : value === false ? '不支持' : '未知';
    const badge = providerNode('span', 'provider-capability', label + ' · ' + status);
    badge.dataset.support = value === true ? 'yes' : value === false ? 'no' : 'unknown';
    badge.title = Object.hasOwn(model.capability_overrides || {}, name) ? '手动设置' : '模型目录';
    capabilities.append(badge);
  }

  const output = providerNode('div', 'provider-model-test');
  const actions = providerNode('div', 'provider-model-actions');
  const test = providerButton('发送测试', () => runModelTest(provider, model));
  test.className = 'provider-model-send';
  test.title = '发送一次实际 API 请求，可能消耗额度';
  test.setAttribute('aria-label', '发送测试 · ' + model.name);
  const detail = providerButton('详情', () => editProviderModel(provider, model));
  detail.setAttribute('aria-label', '模型详情 · ' + model.name);
  actions.append(test, detail);
  row.append(identity, capabilities, output, actions);
  row.dataset.modelRef = testKey(provider, model);
  renderModelTest(output, test, provider, model);
  return row;
}

function renderModelTest(output, button, provider, model) {
  const key = testKey(provider, model);
  const running = runningModelTests.has(key);
  const result = transientModelTests.get(key) || model.last_test;
  button.disabled = running;
  button.textContent = running ? '测试中…' : result ? '重新测试' : '发送测试';
  output.replaceChildren();
  const state = running ? 'busy' : !result ? 'idle' : result.ok ? 'passed' : 'failed';
  const label = {busy: '正在请求', idle: '未测试', passed: '可调用', failed: '测试失败'}[state];
  const duration = Number.isFinite(result?.duration_ms) && !running ? ' · ' + result.duration_ms + ' ms' : '';
  const title = providerNode('span', 'provider-model-state', label + duration);
  title.dataset.state = state;
  output.append(title);
  if (running || !result) {
    output.append(providerNode('code', 'provider-test-route', '/' + model.probe));
    return;
  }
  const details = providerNode('details', 'provider-test-result');
  const summary = providerNode('summary', '', result.summary || '查看请求结果');
  details.append(summary);
  const url = providerNode('code', 'provider-test-url', result.endpoint || '/' + model.probe);
  const time = result.checked_at ? new Date(result.checked_at).toLocaleString('zh-CN', {hour12: false}) : '';
  const usage = result.usage || {};
  const tokens = [usage.input_tokens !== undefined ? '输入 ' + usage.input_tokens : '',
    usage.output_tokens !== undefined ? '输出 ' + usage.output_tokens : ''].filter(Boolean).join(' / ');
  const facts = [result.http_status ? 'HTTP ' + result.http_status : '', time, tokens ? 'Token ' + tokens : ''].filter(Boolean);
  details.append(url, providerNode('p', '', facts.join(' · ')));
  output.append(details);
}

async function runModelTest(provider, model) {
  const key = testKey(provider, model);
  if (runningModelTests.has(key)) return;
  const refreshRow = () => {
    const visible = [...field('api-model-list').querySelectorAll('[data-model-ref]')].find((row) => row.dataset.modelRef === key);
    if (visible) renderModelTest(visible.querySelector('.provider-model-test'), visible.querySelector('.provider-model-send'),
      provider, currentProvider()?.models.find((item) => item.id === model.id) || model);
  };
  runningModelTests.add(key);
  field('api-model-test-live').textContent = model.name + ' 正在发送测试请求';
  refreshRow();
  try {
    const result = await (await request('/api/providers/model/test', {id: provider.id, model: model.id})).json();
    const saved = providerCatalog.providers.find((item) => item.id === provider.id)?.models.find((item) => item.id === model.id);
    if (saved) saved.last_test = result;
    transientModelTests.delete(key);
    field('api-model-test-live').textContent = model.name + ' ' + (result.ok ? '可调用' : '测试失败') + '。' + result.summary;
  } catch (error) {
    transientModelTests.set(key, {ok: false, summary: error.message});
    field('api-model-test-live').textContent = model.name + ' 测试失败。' + error.message;
  } finally {
    runningModelTests.delete(key);
    refreshRow();
  }
}

function renderProviderDefaults({useSaved = false} = {}) {
  const form = field('api-defaults-form');
  for (const kind of Object.keys(providerKinds)) {
    const select = field(`api-default-${kind}`);
    const value = useSaved || form.dataset.dirty !== 'true' ? providerRefValue(providerCatalog.defaults[kind]) : select.value;
    select.replaceChildren(new Option('未设置', ''), ...declaredOptions(kind));
    select.value = [...select.options].some((option) => option.value === value) ? value : '';
  }
  form.querySelector('button').disabled = form.dataset.dirty !== 'true';
}

async function refreshApiProviders() {
  const sequence = ++providerCatalogRequest;
  try {
    const response = await request('/api/providers/list', {});
    const result = await response.json();
    if (sequence !== providerCatalogRequest) return;
    providerCatalog = result;
    providerMessage(field('api-provider-message'), '');
    renderProviders();
    renderProviderDefaults();
    refreshProjectLlmStatus();
    document.dispatchEvent(new Event('prisma-provider-catalog'));
  } catch (error) {
    if (sequence !== providerCatalogRequest) return;
    const output = field('api-provider-message');
    providerMessage(output, `读取 Provider 失败。请确认 WebUI 已重启。 ${error.message}`, true);
    output.append(' ', providerButton('重试', refreshApiProviders));
  }
}

async function checkProvider(id, action) {
  const key = id + ':' + action;
  if (!id || providerBusy.has(key)) return;
  const fetchModels = action === 'models';
  const state = providerChecks.get(id) || {};
  providerChecks.set(id, state);
  providerBusy.add(key);
  state[action] = {message: fetchModels ? '正在获取模型…' : '正在检查模型目录接口…'};
  renderConnectionState();
  try {
    const result = await (await request('/api/providers/' + action, {id})).json();
    state[action] = fetchModels
      ? {message: result.count ? '已同步 ' + result.count + ' 个模型及其 metadata。' : '目录返回 0 个模型。可手动添加。'}
      : {message: '连接成功 · GET /models · ' + result.models + ' 个模型'};
    if (fetchModels) await refreshApiProviders();
  } catch (error) {
    state[action] = {message: fetchModels ? error.message + ' 可手动添加模型。' : error.message, error: true};
  } finally {
    providerBusy.delete(key);
    renderConnectionState();
  }
}

function editProvider(provider) {
  editingProvider = provider;
  field('api-provider-title').textContent = provider ? '编辑 Provider' : '添加 Provider';
  field('api-provider-name').value = provider?.name || '';
  field('api-provider-protocol').value = provider?.protocol || 'openai';
  field('api-provider-url').value = provider?.base_url || '';
  field('api-provider-key').value = '';
  field('api-provider-key').placeholder = provider?.configured ? '留空保留当前 Key' : '服务无需认证时可留空';
  field('api-provider-clear').checked = false;
  field('api-provider-clear-row').hidden = !provider?.configured;
  field('api-provider-delete').hidden = !provider || provider.id === 'openai';
  providerMessage(field('api-provider-edit-error'), '');
  field('api-provider-dialog').showModal();
}

function editProviderModel(provider, model) {
  editingModelProvider = provider;
  field('api-model-title').textContent = model ? '模型详情' : '手动添加模型';
  field('api-model-provider').textContent = provider.name;
  field('api-model-id').value = model?.id || '';
  field('api-model-id').readOnly = Boolean(model);
  field('api-model-name').value = model?.name || '';
  const kind = field('api-model-kind');
  kind.replaceChildren(...Object.entries({...providerKinds, '': '其他'})
    .filter(([value]) => provider.protocol === 'openai' || value === 'text')
    .map(([value, label]) => new Option(label, value)));
  kind.value = model?.kind ?? 'text';
  const features = field('api-model-capabilities');
  features.replaceChildren(...Object.entries(capabilityLabels).map(([key, name]) => {
    const label = providerNode('label', '', name);
    const select = document.createElement('select');
    select.dataset.capability = key;
    const manual = model?.capability_overrides?.[key];
    const reported = typeof manual === 'boolean' ? undefined : model?.capabilities?.[key];
    const hint = reported === true ? '（支持）' : reported === false ? '（不支持）' : '（未确认）';
    select.append(new Option('跟随目录' + hint, ''), new Option('支持 · 手动', 'yes'), new Option('不支持 · 手动', 'no'));
    select.value = typeof manual === 'boolean' ? (manual ? 'yes' : 'no') : '';
    label.append(select);
    return label;
  }));
  const probe = field('api-model-probe');
  const operations = provider.protocol === 'anthropic' ? ['messages'] : probeOperations;
  probe.replaceChildren(new Option('自动选择测试接口', ''),
    ...operations.map((operation) => new Option('/' + operation, operation)));
  probe.value = operations.includes(model?.probe_override) ? model.probe_override : '';
  field('api-model-test-voice').value = model?.test_voice || '';
  updateProbeFields();
  field('api-model-metadata').value = model ? JSON.stringify(model.metadata, null, 2) : '';
  field('api-model-metadata').closest('details').open = false;
  providerMessage(field('api-model-error'), '');
  field('api-model-dialog').showModal();
}

function updateProbeFields() {
  const probe = field('api-model-probe').value;
  field('api-model-test-voice-field').hidden = probe !== 'audio/speech' && (probe || field('api-model-kind').value !== 'audio');
}

field('api-provider-add').onclick = () => editProvider();
field('api-provider-edit').onclick = () => editProvider(currentProvider());
field('api-provider-test-connection').onclick = () => checkProvider(selectedProviderId, 'test');
field('api-provider-fetch').onclick = () => checkProvider(selectedProviderId, 'models');
field('api-model-add').onclick = () => editProviderModel(currentProvider());
field('api-model-query').oninput = field('api-model-filter').onchange = () => {
  modelPage = 0;
  renderModelRows();
};
field('api-model-probe').onchange = field('api-model-kind').onchange = updateProbeFields;
field('api-provider-cancel').onclick = () => field('api-provider-dialog').close();
field('api-model-cancel').onclick = () => field('api-model-dialog').close();
field('api-provider-delete').onclick = async (event) => {
  const button = event.currentTarget, id = editingProvider?.id;
  if (!id || !confirm('删除 ' + editingProvider.name + ' 及其模型声明？')) return;
  button.disabled = true;
  try {
    await request('/api/providers/delete', {id});
    providerChecks.delete(id);
    field('api-provider-dialog').close();
    await refreshApiProviders();
  } catch (error) { providerMessage(field('api-provider-edit-error'), error.message, true); }
  finally { button.disabled = false; }
};
field('api-provider-form').onsubmit = async (event) => {
  event.preventDefault();
  const submit = event.currentTarget.querySelector('[type=submit]');
  submit.disabled = true;
  const payload = {name: field('api-provider-name').value, protocol: field('api-provider-protocol').value,
    base_url: field('api-provider-url').value};
  const key = field('api-provider-key').value;
  if (editingProvider) payload.id = editingProvider.id;
  if (key || field('api-provider-clear').checked) payload.api_key = key;
  try {
    const result = await (await request('/api/providers/save', payload)).json();
    field('api-provider-key').value = '';
    field('api-provider-dialog').close();
    const connectionChanged = !editingProvider || key || field('api-provider-clear').checked
      || result.base_url !== editingProvider.base_url || result.protocol !== editingProvider.protocol;
    if (connectionChanged) {
      providerChecks.delete(result.id);
      for (const model of editingProvider?.models || []) transientModelTests.delete(testKey(editingProvider, model));
    }
    if (selectedProviderId !== result.id) {
      selectedProviderId = result.id;
      modelPage = 0;
      field('api-model-query').value = '';
      field('api-model-filter').value = 'all';
    }
    await refreshApiProviders();
    if (connectionChanged) await checkProvider(result.id, 'models');
  } catch (error) { providerMessage(field('api-provider-edit-error'), error.message, true); }
  finally { submit.disabled = false; }
};

field('api-model-form').onsubmit = async (event) => {
  event.preventDefault();
  const submit = event.currentTarget.querySelector('[type=submit]');
  submit.disabled = true;
  try {
    const metadata = JSON.parse(field('api-model-metadata').value.trim() || '{}');
    const id = editingModelProvider.id;
    const model = field('api-model-id').value.trim();
    const capabilities = Object.fromEntries([...field('api-model-capabilities').querySelectorAll('select')]
      .filter((select) => select.value).map((select) => [select.dataset.capability, select.value === 'yes']));
    await request('/api/providers/model/save', {id, model,
      kind: field('api-model-kind').value, name: field('api-model-name').value, metadata, capabilities,
      probe: field('api-model-probe').value, test_voice: field('api-model-test-voice').value});
    transientModelTests.delete(testKey(editingModelProvider, {id: model}));
    const state = providerChecks.get(id) || {};
    providerChecks.set(id, {...state, models: {message: model + ' 已保存。'}});
    modelPage = 0;
    field('api-model-query').value = '';
    field('api-model-filter').value = 'all';
    field('api-model-dialog').close();
    await refreshApiProviders();
  } catch (error) { providerMessage(field('api-model-error'), error.message, true); }
  finally { submit.disabled = false; }
};

field('api-defaults-form').onchange = (event) => {
  event.currentTarget.dataset.dirty = 'true';
  event.currentTarget.querySelector('button').disabled = false;
  providerMessage(field('api-defaults-status'), '');
};
field('api-defaults-form').onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button');
  button.disabled = true;
  const defaults = Object.fromEntries(Object.keys(providerKinds).map((kind) =>
    [kind, readProviderRef(field(`api-default-${kind}`).value)]));
  try {
    await request('/api/providers/defaults/save', {defaults});
    delete form.dataset.dirty;
    await refreshApiProviders();
    providerMessage(field('api-defaults-status'), '默认模型已保存。');
  } catch (error) {
    providerMessage(field('api-defaults-status'), error.message, true);
    button.disabled = false;
  }
};

document.addEventListener('prisma-project-change', refreshProjectLlmStatus);
document.addEventListener('prisma-view-change', (event) => {
  if (event.detail === 'api-providers') refreshApiProviders();
});

const voiceForm = field('voice-form');
const voicePreview = field('voice-preview');
function updateVoiceMode() {
  const local = field('voice-mode').value === 'local';
  voiceForm.hidden = local;
  field('voice-local').hidden = !local;
  const button = voiceForm.querySelector('button');
  button.disabled = !effectiveNodeModel('voice-node-model') || button.dataset.busy === 'true';
}
field('voice-mode').onchange = updateVoiceMode;
field('voice-import').onclick = () => field('files').click();
let voiceRequest = 0;
voiceForm.onsubmit = async (event) => {
  event.preventDefault();
  if (!openProject) return;
  const project = openProject.id;
  const sequence = ++voiceRequest;
  const submit = voiceForm.querySelector('button');
  const output = field('voice-status');
  submit.disabled = true;
  submit.dataset.busy = 'true';
  providerMessage(output, '正在生成日语配音…');
  try {
    const asset = await generationRequest('voice-assets', '/api/speech/generate', {project_id: project,
      model_override: readProviderRef(field('voice-node-model').value),
      text: field('voice-text').value, voice: field('voice-name').value,
      speed: field('voice-speed').valueAsNumber, line_id: field('voice-line').value});
    if (sequence !== voiceRequest || project !== openProject?.id) return;
    voicePreview.src = asset.url + '?preview=1';
    voicePreview.hidden = false;
    providerMessage(output, '配音已登记为项目素材，可以试听并通过节点。');
    reportNodeOutput('voice-assets', {reference: {kind: 'asset', id: asset.id}, label: asset.name});
  } catch (error) {
    if (sequence === voiceRequest) providerMessage(output, error.message, true);
  } finally {
    if (sequence === voiceRequest) {
      delete submit.dataset.busy;
      updateVoiceMode();
    }
  }
};
document.addEventListener('prisma-project-change', () => {
  voiceRequest++;
  voiceForm.reset();
  refreshNodeModelSelectors();
  const button = voiceForm.querySelector('button');
  button.disabled = false;
  delete button.dataset.busy;
  voicePreview.removeAttribute('src');
  voicePreview.hidden = true;
  providerMessage(field('voice-status'), '');
  field('voice-mode').value = 'provider';
  updateVoiceMode();
});
for (const id of Object.keys(nodeModelSelectors)) {
  const select = field(id);
  select.addEventListener('change', () => {
    const key = nodeModelKey(select);
    if (key) {
      if (select.value) sessionStorage.setItem(key, select.value);
      else sessionStorage.removeItem(key);
    }
    if (id === 'voice-node-model') updateVoiceMode();
  });
}
refreshApiProviders();
