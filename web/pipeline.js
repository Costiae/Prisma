(() => {
  const kinds = {live: '已接通', partial: '手动处理', plan: '尚未接入', reference: '参考输入'};
  const resourceNames = {source: '原文', scenario: '剧本', cues: '演出', prompt: '提示词',
    asset: '素材', job: '任务', artifact: '产物', model: '模型', lora: 'LoRA',
    dataset: '数据集', revision: '项目版本', export: '导出', tool: '工具', script: '脚本'};
  let state;
  let catalog = [];
  let catalogKey = '';
  let catalogRequest = 0;
  let searchTimer;
  let requestNumber = 0;
  let busy = false;
  let showingRoadmap = false;
  let selectedNode = '';
  let mountedRoadmap = '';
  let nodesById = new Map();
  let mapEdges = [];
  let hoveredNode = '';
  let focusedNode = '';
  let activeStage = '';
  let mountedStage = '';
  let mountedPanels = [];
  const previewRequests = new WeakMap();
  let previewSequence = 0;
  const drafts = new Map();
  const outputs = new Map();
  const graph = pipeline.querySelector('.flow-graph');
  const stageView = field('pipeline-stage');
  const tools = field('stage-tools');

  const selected = () => selectedNode;
  const head = (node) => state?.nodes[node];
  const stageTitle = (node) => nodesById.get(node).title;
  const draftKey = () => `${openProject?.id}/${selected()}`;
  const incoming = (node) => state?.roadmap.edges.filter((edge) => edge.target === node) || [];
  const outgoing = (node) => state?.roadmap.edges.filter((edge) => edge.source === node) || [];
  const release = (edge) => {
    const item = head(edge.source);
    return item?.status === 'released' && item.targets.includes(edge.target) ? item : null;
  };

  function inputVersionsChanged(node, item = head(node)) {
    if (!item) return false;
    const current = Object.fromEntries(incoming(node).map(release).filter(Boolean)
      .map((entry) => [entry.node, entry.id]));
    return JSON.stringify(item.inputs || {}) !== JSON.stringify(current);
  }

  function nodeStatus(node) {
    const item = head(node);
    const live = currentOutput(node);
    if (live?.pending || ['pending', 'refine'].includes(item?.status)) return '进行中';
    if (item?.status === 'released') {
      if (inputVersionsChanged(node, item)) return live ? '进行中' : '未完成';
      return '已完成';
    }
    if (item?.status === 'skipped' || skippedByDownstream(node)) return '已跳过';
    if (live || readySnapshot(node)) return '进行中';
    return '未完成';
  }

  function skippedByDownstream(node) {
    const edges = outgoing(node);
    // Skip only when every direct successor is complete, current, and bypassed this node.
    return edges.length > 0 && edges.every(({target}) => {
      const downstream = head(target);
      return downstream?.status === 'released' && !inputVersionsChanged(target, downstream)
        && !Object.hasOwn(downstream.inputs || {}, node);
    });
  }

  function element(tag, text, className) {
    const item = document.createElement(tag);
    if (text) item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  function showMessage(text, error = false) {
    const message = field('stage-message');
    message.hidden = !text;
    message.textContent = text;
    message.classList.toggle('error-text', error);
  }

  function stageLink(node) {
    const link = element('a', stageTitle(node));
    link.href = `#stage-${node}`;
    return link;
  }

  function resourceList(refs, panel = field('stage-resource-preview')) {
    return refs.map((resource) => {
      const row = element('li');
      const link = element('a', `${resourceNames[resource.kind] || resource.kind} · ${resource.label}`);
      link.href = `#${panel.id}`;
      link.addEventListener('click', (event) => {
        event.preventDefault();
        previewResource(resource, panel);
      });
      row.append(link);
      return row;
    });
  }

  async function previewResource(resource, panel = field('stage-resource-preview')) {
    const sequence = ++previewSequence;
    previewRequests.set(panel, sequence);
    const project = openProject?.id;
    panel.hidden = false;
    panel.replaceChildren(element('p', '正在读取产物…'));
    try {
      const response = await request('/api/images/library/item', {kind: resource.kind, id: resource.id});
      const item = await response.json();
      if (sequence !== previewRequests.get(panel) || project !== openProject?.id || !panel.isConnected) return;
      const meta = item.metadata;
      panel.replaceChildren(element('strong', item.label));
      const content = meta.text ?? meta.prompt ?? meta.scenario ?? meta.cues;
      const url = meta.url;
      if (typeof url === 'string' && url.startsWith('/api/')) {
        if (meta.category === 'images' || imageExtensions.test(meta.name || item.label)) {
          const image = element('img');
          image.src = url;
          image.alt = item.label;
          panel.append(image);
        } else if (audioExtensions.test(meta.name || item.label)) {
          const player = element('audio');
          player.src = url;
          player.controls = true;
          player.setAttribute('aria-label', item.label);
          panel.append(player);
        }
        const link = element('a', '打开文件');
        link.href = url;
        panel.append(link);
      }
      if (content != null) panel.append(element('pre',
        typeof content === 'string' ? content : JSON.stringify(content, null, 2), 'stage-version-content'));
      if (meta.family) panel.append(element('p', `底模系列 · ${meta.family}`, 'hint'));
      panel.scrollIntoView({block: 'nearest'});
    } catch (error) {
      if (sequence === previewRequests.get(panel) && project === openProject?.id) {
        panel.replaceChildren(element('p', error.message, 'error-text'));
      }
    }
  }

  function renderVersion(item) {
    const block = document.createDocumentFragment();
    block.append(element('p', `版本 ${item.id.slice(0, 10)}`, 'stage-version-id'));
    if (item.content) block.append(element('pre', item.content, 'stage-version-content'));
    if (item.references.length) {
      const list = element('ul', '', 'stage-reference-list');
      list.append(...resourceList(item.references));
      block.append(list);
    }
    return block;
  }

  const approvedView = field('approved-assets');
  const approvedFilter = field('approved-filter');
  const approvedQuery = field('approved-query');
  const approvedList = field('approved-list');
  const approvedDetail = field('approved-detail');
  const approvedStatus = field('approved-status');
  let approvedItems = [];
  let approvedSelection = '';
  let approvedRequest = 0;
  let approvedDetailRequest = 0;

  async function showApproved(item, force = false) {
    const selection = item?.id || '';
    for (const button of approvedList.querySelectorAll('button')) {
      button.setAttribute('aria-pressed', String(button.dataset.version === selection));
    }
    if (!force && approvedSelection === selection && !approvedDetail.hidden) return;
    approvedSelection = selection;
    const sequence = ++approvedDetailRequest;
    approvedDetail.hidden = !item;
    approvedDetail.replaceChildren();
    if (!item) return;
    approvedDetail.append(element('p', '正在读取成果…', 'hint'));
    const project = openProject.id;
    try {
      const response = await request('/api/pipeline/approved', {project_id: project, node: item.node});
      const result = await response.json();
      if (sequence !== approvedDetailRequest || project !== openProject?.id) return;
      const entry = result.items[0];
      if (entry?.id !== item.id) {
        approvedDetail.replaceChildren(element('p', '成果版本已变更，请刷新列表。', 'hint'));
        return;
      }
      const heading = element('div', '', 'approved-detail-heading');
      heading.append(element('h2', entry.title), element('span', entry.needs_review ? '待复核' : '已通过',
        entry.needs_review ? 'approved-review' : 'approved-passed'));
      const actions = element('div', '', 'approved-actions');
      const open = element('a', '打开节点');
      open.href = `#stage-${entry.node}`;
      const retry = element('button', '退回修改');
      retry.type = 'button';
      retry.addEventListener('click', async () => {
        if (project !== openProject?.id || !confirm(`将「${entry.title}」退回修改？当前结果会停止向下游传递。`)) return;
        retry.disabled = true;
        try {
          await request('/api/pipeline/action', {project_id: project, node: entry.node, decision: 'retry', base: entry.id});
          if (project === openProject?.id) {
            await refreshApproved();
            refresh();
          }
        } catch (error) {
          if (project === openProject?.id) approvedStatus.textContent = error.message;
        } finally {
          retry.disabled = false;
        }
      });
      actions.append(open, retry);
      const version = element('p', `版本 ${entry.id.slice(0, 10)} · ${new Date(entry.updated).toLocaleString('zh-CN')}`,
        'stage-version-id');
      version.title = entry.id;
      approvedDetail.replaceChildren(heading, version, actions);
      if (entry.needs_review) approvedDetail.append(element('p', '直接上游的通过版本已变化。', 'hint'));
      if (entry.content) approvedDetail.append(element('pre', entry.content, 'stage-version-content'));
      if (entry.references.length) {
        const preview = element('div', '', 'stage-resource-preview approved-preview');
        preview.id = 'approved-preview';
        preview.hidden = true;
        const list = element('ul', '', 'stage-reference-list');
        list.append(...resourceList(entry.references, preview));
        approvedDetail.append(element('h3', '关联资源'), list, preview);
      }
    } catch (error) {
      if (sequence === approvedDetailRequest && project === openProject?.id) {
        approvedDetail.replaceChildren(element('p', error.message, 'error-text'));
      }
    }
  }

  function renderApproved(force = false) {
    const query = approvedQuery.value.trim().toLocaleLowerCase();
    const matches = approvedItems.filter((item) => (!approvedFilter.value || approvedFilter.value === item.node)
      && (!query || [item.title, item.id, item.content, ...item.references.flatMap((ref) => [ref.label, ref.id])]
        .some((value) => value.toLocaleLowerCase().includes(query))));
    approvedList.replaceChildren(...matches.map((item) => {
      const row = element('li');
      const button = element('button', '', 'approved-row');
      button.type = 'button';
      button.dataset.version = item.id;
      const heading = element('span', '', 'approved-row-heading');
      heading.append(element('strong', item.title));
      if (item.needs_review) heading.append(element('span', '待复核', 'approved-review'));
      const lead = item.references.find((ref) => ref.kind === 'asset') || item.references[0];
      const summary = lead?.label || item.content || `版本 ${item.id.slice(0, 10)}`;
      const more = item.references.length > 1 ? ` · 另有 ${item.references.length - 1} 项资源` : '';
      button.append(heading, element('span', summary + more, 'approved-row-summary'));
      button.addEventListener('click', () => showApproved(item));
      row.append(button);
      return row;
    }));
    const empty = field('approved-empty');
    empty.hidden = matches.length > 0;
    empty.textContent = approvedItems.length ? '没有符合筛选条件的成果。' : '在任一制作节点点击「通过」，成果就会出现在这里。';
    approvedStatus.textContent = `${matches.length} 项成果`;
    const selected = matches.find((item) => item.id === approvedSelection) || matches[0];
    showApproved(selected, force);
  }

  async function refreshApproved() {
    const project = openProject?.id;
    if (!project || approvedView.hidden) return;
    const sequence = ++approvedRequest;
    field('approved-refresh').disabled = true;
    approvedStatus.textContent = '正在读取已通过成果…';
    field('approved-empty').hidden = true;
    try {
      const response = await request('/api/pipeline/approved', {project_id: project});
      const result = await response.json();
      if (sequence !== approvedRequest || project !== openProject?.id) return;
      approvedItems = result.items;
      const filter = approvedFilter.value;
      approvedFilter.replaceChildren(new Option('全部节点', ''),
        ...approvedItems.map((item) => new Option(item.title, item.node)));
      if (approvedItems.some((item) => item.node === filter)) approvedFilter.value = filter;
      renderApproved(true);
    } catch (error) {
      if (sequence === approvedRequest) approvedStatus.textContent = `无法读取成果。${error.message}`;
    } finally {
      if (sequence === approvedRequest) field('approved-refresh').disabled = false;
    }
  }

  field('approved-refresh').addEventListener('click', refreshApproved);
  approvedQuery.addEventListener('input', () => renderApproved());
  approvedFilter.addEventListener('change', () => renderApproved());

  function stageButton(node, className) {
    const button = element('button', '', `flow-node is-${node.kind || 'live'} ${className}`);
    button.type = 'button';
    button.dataset.node = node.id;
    button.title = node.title;
    const title = element('span', '', 'node-title');
    title.append(element('span', node.title), element('span', '', 'stage-status'));
    button.append(title);
    return button;
  }

  function mountRoadmap() {
    const signature = JSON.stringify(state.roadmap);
    if (signature === mountedRoadmap) return;
    const {nodes, edges} = state.roadmap;
    const layout = layoutRoadmap(state.roadmap);
    nodesById = new Map(nodes.map((node) => [node.id, node]));
    const start = nodes.find(({id}) => !edges.some((edge) => edge.target === id && !edge.feedback));
    field('flow-start').href = `#stage-${start.id}`;
    field('flow-start').hidden = false;
    const svg = pipeline.querySelector('.flow-edges');
    graph.style.width = `${layout.width}px`;
    graph.style.height = `${layout.height}px`;
    svg.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);
    for (const path of mapEdges) path.remove();
    for (const button of graph.querySelectorAll('.graph-node')) button.remove();
    mapEdges = layout.paths.map(({source, target, feedback, path}) => {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      line.classList.add('pipeline-edge');
      if (feedback) line.classList.add('is-feedback');
      line.dataset.from = source;
      line.dataset.to = target;
      line.setAttribute('d', path);
      svg.append(line);
      return line;
    });
    for (const node of nodes) {
      const box = layout.boxes.get(node.id);
      const button = stageButton(node, 'graph-node');
      Object.assign(button.style, {left: `${box.x}px`, top: `${box.y}px`,
        width: `${box.width}px`, height: `${box.height}px`});
      graph.append(button);
    }

    const table = field('flow-ledger-body');
    table.replaceChildren();
    const links = (neighbors, fallback) => {
      const list = element('p', '', 'stage-table-links');
      list.append(...(neighbors.length ? neighbors.map(stageLink) : [document.createTextNode(fallback)]));
      return list;
    };
    for (const node of nodes) {
      const row = table.insertRow();
      const heading = element('th');
      heading.scope = 'row';
      heading.append(stageButton(node, 'flow-table-node'));
      row.append(heading);
      row.insertCell().append(links(incoming(node.id).map((edge) => edge.source), '由作者提供'),
        document.createTextNode(node.input));
      row.insertCell().textContent = node.output;
      row.insertCell().append(links(outgoing(node.id).map((edge) => edge.target), '在此归档'));
    }
    const shortcuts = field('flow-shortcuts');
    const quickLinks = nodes.filter((node) => node.kind !== 'plan' && node.panels?.length).map((node) => {
      const link = stageLink(node.id);
      link.className = 'route-step';
      return link;
    });
    shortcuts.replaceChildren(...quickLinks);
    shortcuts.parentElement.hidden = !quickLinks.length;
    mountedRoadmap = signature;
  }

  function updateMap() {
    for (const line of mapEdges) {
      line.classList.toggle('is-released', Boolean(release({source: line.dataset.from, target: line.dataset.to})));
      line.classList.toggle('is-related', [line.dataset.from, line.dataset.to].includes(selected()));
    }
    for (const button of pipeline.querySelectorAll('.flow-node')) {
      renderNodeStatus(button.querySelector('.stage-status'), button.dataset.node);
      button.setAttribute('aria-pressed', String(button.dataset.node === selected()));
    }
    updateNeighborhood();
  }

  function readySnapshot(node) {
    const snapshot = nodesById.get(node)?.snapshot;
    return Boolean(state && snapshot && pipelineAvailableSnapshots[snapshot]);
  }

  function currentOutput(node) {
    const item = outputs.get(node);
    return item?.fingerprint && item.fingerprint !== editorFingerprint() ? null : item;
  }

  function renderNodeStatus(badge, node) {
    const value = nodeStatus(node);
    const state = {未完成: 'unfinished', 已完成: 'completed', 进行中: 'working', 已跳过: 'skipped'}[value];
    badge.textContent = value;
    badge.dataset.status = state;
    const button = badge.closest('.flow-node');
    if (button) {
      button.dataset.flowStatus = state;
      button.title = `${nodesById.get(node).title} · ${value}`;
    }
  }

  function updateNeighborhood() {
    const id = hoveredNode || focusedNode;
    const active = Boolean(state && nodesById.has(id));
    const upstream = new Set();
    const downstream = new Set();
    for (const line of mapEdges) {
      const {from, to} = line.dataset;
      const enters = active && to === id;
      const leaves = active && from === id;
      line.classList.toggle('is-neighbor', enters || leaves);
      line.classList.toggle('is-upstream', enters);
      line.classList.toggle('is-downstream', leaves);
      if (enters) upstream.add(from);
      if (leaves) downstream.add(to);
    }
    graph.classList.toggle('has-neighborhood', active);
    field('flow-neighborhood-legend').hidden = !active;
    field('flow-neighborhood-both').hidden = ![...upstream].some((node) => downstream.has(node));
    for (const button of graph.querySelectorAll('.graph-node')) {
      const node = button.dataset.node;
      const before = upstream.has(node);
      const after = downstream.has(node);
      if (active && node === id) button.dataset.neighbor = 'focus';
      else if (before && after) button.dataset.neighbor = 'both';
      else if (before) button.dataset.neighbor = 'upstream';
      else if (after) button.dataset.neighbor = 'downstream';
      else delete button.dataset.neighbor;
    }
  }

  function graphNode(target) {
    return target instanceof Element ? target.closest('.graph-node') : null;
  }

  graph.addEventListener('pointerover', (event) => {
    if (event.pointerType === 'touch') return;
    const node = graphNode(event.target);
    if (!node || node.dataset.node === hoveredNode) return;
    hoveredNode = node.dataset.node;
    updateNeighborhood();
  });
  graph.addEventListener('pointerout', (event) => {
    const node = graphNode(event.target);
    if (!node || node.dataset.node !== hoveredNode) return;
    const next = graphNode(event.relatedTarget);
    if (next === node) return;
    hoveredNode = next?.dataset.node || '';
    updateNeighborhood();
  });
  graph.addEventListener('focusin', (event) => {
    const node = graphNode(event.target);
    if (!node?.matches(':focus-visible')) return;
    focusedNode = node.dataset.node;
    updateNeighborhood();
  });
  graph.addEventListener('focusout', (event) => {
    if (graphNode(event.target)?.dataset.node !== focusedNode) return;
    focusedNode = '';
    updateNeighborhood();
  });

  function updateActivity() {
    const node = nodesById.get(selected());
    if (!node || !state || !activeStage) return;
    const summary = pipelineSummaries[node.activity || node.snapshot] || kinds[node.kind || 'live'];
    field('flow-detail-state').textContent = `工作区 · ${summary}`;
    const snapshot = {source: '原文', scenario: '剧本', assets: '项目素材', cues: '演出绑定'}[node.snapshot];
    const live = currentOutput(node.id);
    field('stage-next-step').textContent = pickedResource()
      ? '通过时使用所选的已有产物。'
      : readySnapshot(node.id) ? `当前项目已有${snapshot}。通过时使用当前版本。`
      : live?.pending ? '正在运行。本次任务完成后可通过。'
      : live?.label ? `当前成果 · ${live.label}`
      : '在本页制作、修改或选用产物，然后在底部通过。';
  }

  function svgElement(tag, attrs = {}, content = '') {
    const item = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attrs)) item.setAttribute(name, value);
    if (content) item.textContent = content;
    return item;
  }

  function renderNeighborhood(node) {
    const upstream = incoming(node);
    const downstream = outgoing(node);
    const maximum = Math.max(1, upstream.length, downstream.length);
    const rowGap = 88;
    const boxWidth = 152;
    const boxHeight = 66;
    const height = Math.max(90, (maximum - 1) * rowGap + boxHeight + 12);
    const mid = height / 2;
    const width = 3 * boxWidth + 2 * 36;
    const columns = {upstream: 0, current: boxWidth + 36, downstream: (boxWidth + 36) * 2};
    const container = field('stage-neighborhood');
    container.style.width = `${width}px`;
    container.style.height = `${height}px`;
    container.setAttribute('aria-label', `直接上游 ${upstream.length} 个，当前节点 ${stageTitle(node)}，直接下游 ${downstream.length} 个`);
    container.replaceChildren();
    const svg = svgElement('svg', {class: 'flow-edges stage-mini-edges',
      viewBox: `0 0 ${width} ${height}`, 'aria-hidden': 'true'});
    const defs = svgElement('defs');
    for (const [id, fill] of [['stage-arrow-live', '#126456'], ['stage-arrow-plan', '#86948e']]) {
      const marker = svgElement('marker', {id, markerUnits: 'userSpaceOnUse', markerWidth: 10, markerHeight: 10,
        refX: 9, refY: 5, orient: 'auto'});
      marker.append(svgElement('path', {fill, d: 'M0 0 L10 5 L0 10 Z'}));
      defs.append(marker);
    }
    svg.append(defs);
    container.append(svg);

    const addNode = (id, x, y, relation) => {
      const button = stageButton(nodesById.get(id), 'graph-node stage-mini-node');
      Object.assign(button.style, {left: `${x}px`, top: `${y - boxHeight / 2}px`,
        width: `${boxWidth}px`, height: `${boxHeight}px`});
      button.dataset.neighbor = relation;
      button.setAttribute('aria-pressed', String(relation === 'focus'));
      button.setAttribute('aria-label', `${stageTitle(id)} · ${nodeStatus(id)}${relation === 'focus' ? ' · 当前节点' : ''}`);
      renderNodeStatus(button.querySelector('.stage-status'), id);
      container.append(button);
    };

    for (const [index, edge] of upstream.entries()) {
      const y = mid + (index - (upstream.length - 1) / 2) * rowGap;
      const path = svgElement('path', {d: `M${columns.upstream + boxWidth} ${y} H${columns.current - 18} V${mid} H${columns.current - 2}`,
        class: `pipeline-edge stage-mini-edge is-neighbor${release(edge) ? ' is-released' : ''}`});
      svg.append(path);
      addNode(edge.source, columns.upstream, y, 'upstream');
    }
    for (const [index, edge] of downstream.entries()) {
      const y = mid + (index - (downstream.length - 1) / 2) * rowGap;
      const path = svgElement('path', {d: `M${columns.current + boxWidth} ${mid} H${columns.downstream - 18} V${y} H${columns.downstream - 2}`,
        class: `pipeline-edge stage-mini-edge is-neighbor${release(edge) ? ' is-released' : ''}`});
      svg.append(path);
      addNode(edge.target, columns.downstream, y, 'downstream');
    }
    addNode(node, columns.current, mid, 'focus');
  }

  function renderInputs(node) {
    nodeInputs = incoming(node).map(release).filter(Boolean).flatMap((entry) => entry.references);
    if (node === 'export') updateWorkspaceStatus();
    renderNeighborhood(node);
  }

  function draft() {
    const key = draftKey();
    if (!drafts.has(key)) {
      const item = head(selected());
      drafts.set(key, {content: item?.content || ''});
    }
    return drafts.get(key);
  }

  function passPayload() {
    const node = selected();
    const live = currentOutput(node);
    const previous = head(node);
    const inputsChanged = inputVersionsChanged(node, previous);
    const content = draft().content.trim();
    const payload = content ? {content} : {};
    const resource = pickedResource();
    if (resource) payload.references = [{kind: resource.kind, id: resource.id}];
    else if (node === 'scenario' && live?.reference) payload.references = [live.reference];
    else if (live?.captureProject || (readySnapshot(node) && !inputsChanged)) payload.capture_project = true;
    else if (node === 'prompt' && field('image-prompt').value.trim()) {
      payload.output = {prompt: field('image-prompt').value, negative: field('image-negative').value};
    } else if (node === 'online') {
      const ref = providerCatalog.defaults.image;
      if (ref) payload.references = [{kind: 'model', id: `${ref.provider_id}:${ref.model}`}];
    } else if (live?.reference) payload.references = [live.reference];
    else if (live?.output) payload.output = live.output;
    else if (head(node)?.status === 'pending' && head(node).references.length) {
      payload.references = head(node).references;
    }
    if (previous && inputsChanged && !live && !resource
        && draft().content === previous.content) return {};
    return payload;
  }

  function updateButtons() {
    if (!state || !activeStage || !nodesById.has(selected())) return;
    const resource = pickedResource();
    const running = currentOutput(selected())?.pending || tools.querySelector('[data-busy=true]');
    field('stage-retry').disabled = busy || Boolean(running);
    field('stage-pass').disabled = busy || Boolean(running) || !Object.keys(passPayload()).length;
    field('stage-view-resource').disabled = !resource;
    field('stage-use-lora').hidden = resource?.kind !== 'lora';
    field('stage-use-lora').disabled = busy;
  }

  function resourceKey({kind, id}) {
    return `${kind}:${id}`;
  }

  function pickedResource() {
    const key = field('stage-resource').value;
    return catalog.find((item) => resourceKey(item) === key);
  }

  async function loadResources() {
    if (!state || !activeStage || !nodesById.has(selected()) || !openProject) return;
    const node = selected();
    const query = field('stage-resource-query').value.trim();
    const key = `${draftKey()}|${query}`;
    if (key === catalogKey) return;
    catalogKey = key;
    const sequence = ++catalogRequest;
    const select = field('stage-resource');
    const old = select.value;
    const status = field('stage-resource-status');
    catalog = [];
    select.replaceChildren(new Option('正在查找资源…', ''));
    status.textContent = '';
    updateButtons();
    try {
      const response = await request('/api/pipeline/reusable', {...projectScope(), node, query});
      const result = await response.json();
      if (sequence !== catalogRequest || key !== catalogKey || node !== selected()) return;
      catalog = result.items;
      select.replaceChildren(new Option(catalog.length ? '请选择产物' : '没有匹配的产物', ''),
        ...catalog.map((item) => new Option(`${resourceNames[item.kind] || item.kind} · ${item.label}${item.shared ? ' · 共享' : ''}`,
          resourceKey(item))));
      if (catalog.some((item) => resourceKey(item) === old)) select.value = old;
      updateButtons();
      updateActivity();
    } catch (error) {
      if (sequence !== catalogRequest) return;
      select.replaceChildren(new Option('资源列表读取失败', ''));
      status.textContent = error.message;
      catalogKey = '';
    }
  }

  function restorePanels() {
    for (const {panel, marker, wasOpen} of mountedPanels) {
      marker.replaceWith(panel);
      if (panel instanceof HTMLDetailsElement) panel.open = wasOpen;
    }
    mountedPanels = [];
    mountedStage = '';
    nodeInputs = [];
  }

  function mountPanels(definition) {
    if (mountedStage === definition.id) return false;
    restorePanels();
    for (const id of definition.panels || []) {
      const panel = field(id);
      if (!panel) continue;
      const marker = document.createComment(`位置 · ${id}`);
      panel.before(marker);
      mountedPanels.push({panel, marker, wasOpen: panel.open});
      tools.append(panel);
      if (panel instanceof HTMLDetailsElement) panel.open = true;
    }
    mountedStage = definition.id;
    stageView.dataset.node = definition.id;
    field('stage-manual').hidden = Boolean(mountedPanels.length);
    return true;
  }

  function renderOutputs() {
    const definition = nodesById.get(selected());
    const live = currentOutput(selected());
    const slot = field('stage-updated-output');
    slot.replaceChildren();
    if (live?.reference?.kind === 'export') {
      const link = element('a', '下载当前 Ren’Py ZIP');
      link.href = `/api/exports/${encodeURIComponent(live.reference.id)}/prisma-renpy.zip`;
      link.download = 'prisma-renpy.zip';
      slot.append(link);
    }
    if (definition?.id === 'prompt' && field('image-prompt').value.trim()) {
      slot.append(element('p', '通过后可把提示词传给下游图像生成。', 'hint'));
    }
  }

  function render() {
    updateMap();
    const node = selected();
    const definition = nodesById.get(node);
    field('stage-controls').hidden = !activeStage || !state || !definition;
    if (!activeStage) return;
    renderNodeStatus(field('stage-node-state'), node);
    if (!state || !definition) {
      field('flow-detail-title').textContent = '节点工作台';
      field('flow-detail-state').textContent = '';
      return;
    }
    field('stage-load-status').hidden = true;
    const newTools = mountPanels(definition);
    stageView.dataset.workspaceView = definition.title;
    updateWorkspaceTitle(stageView);
    const item = head(node);
    field('flow-detail-title').textContent = definition.title;
    field('flow-detail-input').textContent = definition.input;
    field('flow-detail-output').textContent = definition.output;
    renderInputs(node);
    if (newTools) {
      if (node === 'scenario') refreshProjectLlmStatus();
      document.dispatchEvent(new CustomEvent('prisma-node-tools', {detail: node}));
    }
    field('stage-version').hidden = !item;
    field('stage-version').replaceChildren(...(item ? [renderVersion(item)] : []));
    field('stage-content').value = draft().content;
    updateActivity();
    renderOutputs();
    updateButtons();
    loadResources();
  }

  async function refresh() {
    const scope = openProject?.id;
    const sequence = ++requestNumber;
    if (!scope) {
      state = null;
      catalog = [];
      render();
      return;
    }
    const loading = field('stage-load-status');
    loading.hidden = Boolean(state);
    loading.textContent = '正在读取项目节点…';
    try {
      const response = await request('/api/pipeline/state', {project_id: scope});
      const result = await response.json();
      if (sequence !== requestNumber || scope !== openProject?.id) return;
      state = result;
      mountRoadmap();
      if (!nodesById.has(selected()) && !activeStage) selectedNode = field('flow-start').hash.slice(7);
      if (activeStage && !nodesById.has(activeStage)) throw new Error('该节点已不在当前路线图中。');
      catalogKey = '';
      loading.hidden = true;
      render();
    } catch (error) {
      if (sequence !== requestNumber) return;
      loading.hidden = false;
      loading.textContent = `无法读取项目节点：${error.message}`;
    }
  }

  async function decide(decision) {
    if (busy || !state || !activeStage) return;
    const project = openProject.id;
    const node = selected();
    const payload = {project_id: project, node, base: head(node)?.id || '', decision};
    if (decision === 'pass') {
      Object.assign(payload, passPayload(), {
        inputs: Object.fromEntries(incoming(node).map(release).filter(Boolean).map((entry) => [entry.node, entry.id]))});
    }
    busy = true;
    updateButtons();
    showMessage(decision === 'pass' ? '正在传递节点结果…' : '正在重新开始本节点…');
    try {
      if (payload.capture_project) await flushProjectWrite();
      if (project !== openProject?.id) return;
      const response = await request('/api/pipeline/action', payload);
      const result = await response.json();
      if (project !== openProject?.id || !state) return;
      ++requestNumber;
      Object.assign(state.nodes, result.changed);
      if (activeStage !== node) return updateMap();
      render();
      if (decision === 'pass') {
        const targets = outgoing(node).map((edge) => stageTitle(edge.target));
        showMessage(targets.length ? `已通过，结果已传给${targets.join('、')}。` : '已通过，节点已完成。');
      } else {
        const retry = field(nodesById.get(node).retry_control);
        if (retry && tools.contains(retry) && !retry.disabled) {
          showMessage('正在用本页参数重新运行。');
          retry.dataset.stageRetry = 'true';
          retry.click();
          delete retry.dataset.stageRetry;
        } else if (readySnapshot(node)) {
          reportNodeOutput(node, {captureProject: true, label: '当前项目版本', fingerprint: editorFingerprint()});
          showMessage('当前项目内容已载入本节点。');
        } else {
          showMessage('调整本节点设置或成果后即可继续。');
          (tools.querySelector('textarea') || tools.querySelector('select, input:not([type=hidden], [type=file])')
            || field('stage-content')).focus({preventScroll: true});
        }
      }
    } catch (error) {
      if (project === openProject?.id && activeStage === node) showMessage(error.message, true);
    } finally {
      busy = false;
      updateButtons();
    }
  }

  field('stage-content').addEventListener('input', (event) => {
    draft().content = event.target.value;
    updateButtons();
  });
  field('stage-resource').addEventListener('change', () => { updateButtons(); updateActivity(); });
  field('stage-resource-query').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadResources, 250);
  });
  field('stage-view-resource').onclick = () => {
    const resource = pickedResource();
    if (resource) previewResource(resource);
  };
  field('stage-use-lora').onclick = () => {
    const resource = pickedResource();
    if (resource?.kind === 'lora') {
      document.dispatchEvent(new CustomEvent('prisma-use-lora', {detail: {id: resource.id, node: selected()}}));
    }
  };
  field('stage-retry').onclick = () => decide('retry');
  field('stage-pass').onclick = () => decide('pass');
  stageView.addEventListener('click', (event) => {
    const neighbor = event.target.closest('#stage-neighborhood .stage-mini-node');
    if (neighbor) {
      goWorkspace(`stage-${neighbor.dataset.node}`);
      return;
    }
    const link = event.target.closest('a[href^="#"]');
    const target = link && field(link.hash.slice(1));
    if (target && tools.contains(target)) {
      event.preventDefault();
      goWorkspace(target.id);
    }
  });
  tools.addEventListener('input', () => { updateButtons(); updateActivity(); renderOutputs(); });
  tools.addEventListener('change', updateButtons);
  document.addEventListener('prisma-provider-catalog', updateButtons);
  new MutationObserver(updateButtons).observe(tools, {attributes: true, attributeFilter: ['data-busy'], subtree: true});
  document.addEventListener('prisma-node-output', ({detail}) => {
    const old = outputs.get(detail.node);
    if (detail.result) outputs.set(detail.node, detail.result);
    else outputs.delete(detail.node);
    if (detail.node === activeStage) {
      if (old?.pending && detail.result && !detail.result.pending) {
        catalogKey = '';
        loadResources();
      }
      updateActivity();
      renderOutputs();
      updateButtons();
    }
  });
  document.addEventListener('prisma-before-view-change', ({detail}) => {
    if (detail === activeStage) return;
    restorePanels();
    activeStage = '';
    const preview = field('stage-resource-preview');
    previewRequests.delete(preview);
    preview.hidden = true;
  });
  document.addEventListener('prisma-stage-change', ({detail}) => {
    const changed = activeStage !== detail;
    activeStage = detail;
    selectedNode = detail;
    if (state && !nodesById.has(detail)) {
      field('flow-detail-title').textContent = '未找到该节点';
      field('stage-controls').hidden = true;
      field('stage-load-status').hidden = false;
      field('stage-load-status').textContent = '该节点已不在当前路线图中。';
      return;
    }
    if (changed) {
      field('stage-resource-query').value = '';
      field('stage-compose').open = !nodesById.get(detail)?.panels?.length;
      showMessage('');
    }
    render();
  });
  document.addEventListener('prisma-pipeline-activity', () => {
    if (!state) return;
    updateMap();
    if (!activeStage) return;
    renderNodeStatus(field('stage-node-state'), selected());
    updateActivity();
    updateButtons();
  });
  document.addEventListener('prisma-pipeline-refresh', refresh);
  document.addEventListener('prisma-project-change', () => {
    ++approvedRequest;
    approvedItems = [];
    approvedQuery.value = '';
    approvedFilter.replaceChildren(new Option('全部节点', ''));
    approvedList.replaceChildren();
    field('approved-refresh').disabled = false;
    approvedStatus.textContent = '';
    showApproved(null);
    state = null;
    outputs.clear();
    restorePanels();
    activeStage = '';
    catalog = [];
    catalogKey = '';
    ++catalogRequest;
    field('stage-resource-query').value = '';
    hoveredNode = '';
    focusedNode = '';
    showMessage('');
    render();
    refresh();
  });
  document.addEventListener('prisma-view-change', (event) => {
    if (event.detail === 'approved-assets') refreshApproved();
    const onRoadmap = ['projects', 'pipeline-stage'].includes(event.detail);
    if (onRoadmap && !showingRoadmap && openProject && state) refresh();
    showingRoadmap = onRoadmap;
  });
})();

