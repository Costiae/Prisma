(() => {
  const get = (name) => field(`image-${name}`);
  const value = (name) => get(name).value;
  const numeric = (name) => get(name).valueAsNumber;
  const api = '/api/images';
  const presets = {
    portrait: 'Head and shoulders portrait of a young woman with warm chestnut hair in a neat low bun and clear teal eyes, calm expression, crimson and navy 19th-century officer jacket with fine gold braid, pale blue stone courtyard, delicate colored outlines, vivid colors and crisp flat cel shadows, Japanese anime visual novel illustration.',
    duo: 'Two distinct young women walking together through a bright botanical garden. The woman on the left has short dark teal hair and a white and coral dress. The woman on the right has long honey blonde hair and a cobalt blue and ivory officer uniform. Friendly conversation, natural gestures, balanced full figure composition, sunny spring afternoon, vivid colors and crisp flat cel shadows, Japanese anime visual novel illustration.',
    library: 'A young woman with long dark brown hair and a simple ribbon, cream cardigan and teal pleated skirt, reading an open book beside a wooden library desk, warm light from a tall window, bookshelves and stacked books, candid three-quarter view, colored outlines, vivid colors and crisp flat cel shadows, Japanese anime visual novel illustration.',
  };
  const activeState = (job) => ['running', 'cancelling'].includes(job?.state);
  const kindLabels = {generate: '生成', train: '训练', install: '安装'};
  const stateLabels = {running: '进行中', cancelling: '正在取消', done: '完成', failed: '失败', cancelled: '已取消', interrupted: '已中断'};
  let catalog = null;
  let currentJob = '';
  let activeJob = null;
  let timer;
  let submitting = false;
  let datasetBusy = false;
  let sourceItems = [];
  let sourceRequest = 0;
  const addedImages = new Map();
  const uploadedCaptions = new Map();
  let lastGallery = '';

  function report(text, error = false) {
    get('status').textContent = text;
    get('status').classList.toggle('error', error);
  }

  async function json(path, data) {
    if (data !== undefined) return (await request(`${api}${path}`, data)).json();
    const response = await fetch(`${api}${path}`);
    if (!response.ok) throw new Error((await response.json()).error);
    return response.json();
  }

  function retainOptions(select, options, preferred) {
    const previous = preferred ?? select.value;
    select.replaceChildren(...options);
    if (options.some((option) => option.value === previous)) select.value = previous;
  }

  function mode() {
    return get('form').querySelector('[name="image-mode"]:checked').value;
  }

  function controls() {
    const local = mode() === 'local';
    const environment = catalog?.local;
    const working = Boolean(activeJob || submitting);
    const ready = local ? environment?.supported && environment.engine && environment.model : catalog?.online.ready;
    get('local').hidden = get('local').disabled = !local;
    get('online').hidden = get('online').disabled = local;
    field('local-toolkit').hidden = !local;
    get('mode-note').textContent = local
      ? (ready ? '本地出图使用当前底模和所选 LoRA。' : '新环境请展开本地工具箱，拉取推理组件或完整 LoRA 工具链。')
      : (catalog?.online.ready ? '生成时会把提示词发送给 OpenAI，并使用账户额度。' : '请设置服务端 OPENAI_API_KEY 并重启 WebUI。');
    get('generate').disabled = working || !ready;
    get('install').disabled = working || !environment?.supported || Boolean(environment.engine && environment.model);
    get('install-train').disabled = working || !environment?.supported || Boolean(environment.engine && environment.model && environment.trainer);
    get('train').disabled = working || !environment?.supported || !environment?.trainer || !environment?.model || !value('train-dataset');
    get('replay').disabled = working;
    const comparing = value('compare') !== 'none';
    get('count').max = local && comparing ? '2' : '4';
    if (numeric('count') > Number(get('count').max)) get('count').value = get('count').max;
    get('weight').disabled = !value('lora') || comparing;
  }

  function setHistory(rows, preferred) {
    const choices = rows.map((job) => {
      const time = new Date(job.started).toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
      return new Option(`${kindLabels[job.kind]} · ${stateLabels[job.state]} · ${time} · ${job.id.slice(0, 6)}`, job.id);
    });
    retainOptions(get('history'), choices.length ? choices : [new Option('暂无任务', '')], preferred);
  }

  async function refresh(preferredJob) {
    const first = !catalog;
    catalog = await json('/catalog');
    const options = catalog.loras.map((lora) => new Option(lora.label, lora.id));
    retainOptions(get('lora'), [new Option('底模', ''), ...options], first && catalog.loras.some((x) => x.id === 'r2-200') ? 'r2-200' : undefined);
    retainOptions(get('compare'), [new Option('关闭', 'none'), new Option('底模', ''), ...options.map((option) => option.cloneNode(true))]);
    retainOptions(get('model'), catalog.online.models.map((name) => new Option(name, name)));
    retainOptions(get('dataset-source'), [new Option('仅使用上传图片', ''), ...catalog.sources.map((s) => new Option(s.label, s.id))]);
    retainOptions(get('train-dataset'), [new Option('先保存数据集', ''), ...catalog.sources.filter((s) => s.saved).map((s) => new Option(s.label, s.id))]);
    get('r2r3').hidden = !['r2-200', 'r3-200'].every((id) => catalog.loras.some((lora) => lora.id === id));

    const env = catalog.local;
    const status = (path) => path || (env.supported ? '尚未安装' : '安装支持 Windows x64');
    const info = [['推理工具', status(env.engine)], ['底模', status(env.model)], ['LoRA 训练环境', status(env.trainer)], ['训练设备', env.training_device || 'NVIDIA CUDA'], ['参考目录', env.reference || 'Reference/Img'], ['工作目录', env.workspace]];
    get('environment').replaceChildren(...info.flatMap(([name, result]) => {
      const term = document.createElement('dt');
      const desc = document.createElement('dd');
      term.textContent = name;
      desc.textContent = result;
      return [term, desc];
    }));
    activeJob = catalog.jobs.find(activeState)?.id ?? null;
    setHistory(catalog.jobs, preferredJob ?? (currentJob || activeJob || undefined));
    currentJob = value('history');
    controls();
    await show(currentJob);
    schedulePoll();
  }

  function renderJob(job) {
    const phase = get('phase');
    phase.textContent = job ? [job.phase, job.error || job.note, job.kind === 'train' ? job.options.dataset_name : null].filter(Boolean).join(' · ') : '生成或训练完成后，可在这里查看产物。';
    phase.classList.toggle('error-text', job?.state === 'failed');
    get('cancel').hidden = !activeState(job) || job.options.mode === 'online';
    get('cancel').disabled = job?.state === 'cancelling';
    get('reuse').hidden = job?.kind !== 'generate';
    get('reuse').onclick = () => reuse(job.options);
    get('replay').hidden = !job || activeState(job) || job.options.mode === 'online';
    get('replay').onclick = () => submitJob('/replay', {id: job.id});
    const progress = get('progress');
    progress.hidden = !activeState(job);
    if (job?.progress?.total) {
      progress.value = 100 * job.progress.bytes / job.progress.total;
      phase.textContent += ` · ${(job.progress.bytes / 1048576).toFixed(0)} / ${(job.progress.total / 1048576).toFixed(0)} MiB`;
    } else progress.removeAttribute('value');
    get('log-box').hidden = !job?.log;
    if (get('log').textContent !== (job?.log || '')) get('log').textContent = job?.log || '';

    const canUse = (item) => catalog?.loras.some((lora) => lora.id === item.id);
    const signature = JSON.stringify([job?.id, job?.images, job?.checkpoints, job?.checkpoints.map(canUse)]);
    if (signature === lastGallery) return;
    lastGallery = signature;
    const artifactURL = (category, name) => `${api}/files/${job.id}/${category}/${encodeURIComponent(name)}`;
    const gallery = get('gallery');
    gallery.replaceChildren();
    get('checkpoints').replaceChildren();
    gallery.classList.toggle('is-comparison', job?.options.loras?.length === 2);
    for (const item of job?.images || []) {
      const url = artifactURL('images', item.name);
      const name = `prisma-${job.id}-${item.name}`;
      const card = document.createElement('figure');
      const preview = document.createElement('a');
      preview.href = url;
      preview.target = '_blank';
      preview.rel = 'noopener';
      const title = [item.label, item.weight != null ? `权重 ${item.weight}` : '', item.seed != null ? `Seed ${item.seed}` : ''].filter(Boolean).join(' · ');
      const image = document.createElement('img');
      image.src = url;
      image.alt = title;
      image.loading = 'lazy';
      preview.append(image);
      const caption = document.createElement('figcaption');
      caption.textContent = title;
      const actions = document.createElement('div');
      actions.className = 'studio-actions';
      const add = document.createElement('button');
      add.type = 'button';
      add.textContent = '加入素材';
      add.onclick = async () => {
        if (uploads.has(name)) return report('图片已经在当前工程素材中。');
        add.disabled = true;
        try {
          const response = await fetch(url);
          if (!response.ok) throw new Error('无法读取候选图片。');
          uploads.set(name, new File([await response.blob()], name, {type: 'image/png'}));
          renderFiles();
          if (['show', 'background'].includes(action.value)) asset.value = name;
          report('图片已加入素材，可在「演出与素材」绑定到台词。');
        } catch (error) {
          report(error.message, true);
        } finally { add.disabled = false; }
      };
      const save = document.createElement('a');
      save.href = url;
      save.download = name;
      save.textContent = '下载';
      actions.append(add, save);
      card.append(preview, caption, actions);
      gallery.append(card);
    }
    for (const item of job?.checkpoints || []) {
      const file = document.createElement('a');
      file.href = artifactURL('checkpoints', item.name);
      file.textContent = `下载 ${item.label}`;
      file.download = item.name;
      const use = document.createElement('button');
      use.type = 'button';
      use.textContent = '试画';
      use.setAttribute('aria-label', `试画 ${item.label}`);
      use.disabled = !canUse(item);
      use.onclick = () => {
        setMode('local');
        get('compare').value = 'none';
        get('lora').value = item.id;
        get('lora').onchange();
        get('prompt').focus();
        report(`已选中 ${item.label}，填写提示词后即可生成。`);
      };
      const row = document.createElement('div');
      row.className = 'studio-actions checkpoint-row';
      row.append(file, use);
      get('checkpoints').append(row);
    }
  }

  function setMode(value) {
    get('form').querySelector(`[name="image-mode"][value="${value}"]`).checked = true;
  }

  function reuse(options) {
    if (options.mode === 'local' && options.loras.some((x) => x.id && !catalog.loras.some((lora) => lora.id === x.id))) {
      return report('该任务使用的 LoRA 已不可用。请重新导入权重。', true);
    }
    setMode(options.mode);
    const values = {prompt: options.prompt, count: options.count};
    if (options.mode === 'local') {
      const size = `${options.width}x${options.height}`;
      if (![...get('local-size').options].some((option) => option.value === size)) {
        get('local-size').add(new Option(size, size));
      }
      Object.assign(values, {negative: options.negative, seed: options.seed, steps: options.steps, cfg: options.cfg,
        sampler: options.sampler, 'local-size': size, lora: options.loras[0].id,
        compare: options.loras[1]?.id ?? 'none', weight: options.loras[0].weight ?? 0.35});
    } else Object.assign(values, {model: options.model, 'online-size': options.size, quality: options.quality});
    for (const [id, setting] of Object.entries(values)) get(id).value = setting;
    controls();
    report('已恢复本次任务的生成参数。');
  }

  async function show(id) {
    if (!id) return renderJob(null);
    const job = await json(`/jobs/${id}`);
    if (currentJob === id) renderJob(job);
    return job;
  }

  function schedulePoll() {
    clearTimeout(timer);
    if (!activeJob) return;
    timer = setTimeout(async () => {
      try {
        const observed = activeJob;
        const job = await show(observed);
        if (activeState(job)) {
          if (activeJob === observed) schedulePoll();
        } else {
          activeJob = null;
          report(job.error || `${kindLabels[job.kind]}${stateLabels[job.state]}。`, job.state === 'failed');
          await refresh();
        }
      } catch (error) {
        report(error.message, true);
        schedulePoll();
      }
    }, 1400);
  }

  async function submitJob(route, data) {
    submitting = true;
    controls();
    try {
      const job = await json(route, data);
      activeJob = activeState(job) ? job.id : null;
      currentJob = job.id;
      report('任务已提交。');
      await refresh(job.id);
    } catch (error) {
      report(error.message, true);
      if (activeJob) schedulePoll();
    } finally {
      submitting = false;
      controls();
    }
  }

  get('refresh').onclick = async () => {
    try {
      await refresh();
      report('图像环境已刷新。');
    } catch (error) { report(error.message, true); }
  };
  get('history').onchange = () => {
    currentJob = value('history');
    show(currentJob).catch((error) => report(error.message, true));
  };
  get('cancel').onclick = async () => {
    get('cancel').disabled = true;
    try {
      await json('/cancel', {id: currentJob});
      await refresh();
    } catch (error) { report(error.message, true); }
  };
  get('install').onclick = () => submitJob('/install', {target: 'inference'});
  get('install-train').onclick = () => submitJob('/install', {target: 'training'});
  get('form').querySelectorAll('[name="image-mode"]').forEach((input) => input.onchange = controls);
  get('compare').onchange = controls;
  get('lora').onchange = () => {
    get('weight').value = catalog?.loras.find((x) => x.id === value('lora'))?.weight ?? 0.35;
    controls();
  };
  get('preset').onchange = () => {
    const prompt = presets[value('preset')];
    if (prompt) get('prompt').value = `${mode() === 'local' ? 'prismaCel, ' : ''}${prompt}`;
    get('preset').value = '';
  };
  get('scene').onclick = async () => {
    const scene = inspectedText === scenario.value && scenes[Number(sceneSelect.value)];
    if (!scene) return report('请先解析 Scenario，再选择要制作图片的场景。', true);
    try {
      const kind = action.value === 'background' ? 'background' : action.value === 'show' ? 'sprite' : 'cg';
      const response = await request('/api/asset-prompt', {scenario: scenario.value, scene: scene.id, kind,
        line: selectedLine?.index ?? 1, brief: value('prompt').trim().slice(0, 3000)});
      const result = await response.json();
      const context = result.context.map((line) => `${line.speaker ? `${line.speaker}：` : ''}${line.text}`).join('\n').slice(0, 700);
      get('prompt').value = `${result.prompt}\n\n场景参考（${result.chapter} · ${result.scene}）：\n${context}`.slice(0, 4000);
      get('prompt').focus();
      report('已带入场景和素材构图建议。');
    } catch (error) { report(error.message, true); }
  };
  get('r2r3').onclick = () => {
    get('lora').value = 'r2-200';
    get('compare').value = 'r3-200';
    get('weight').value = '0.35';
    get('steps').value = '20';
    get('cfg').value = '6.5';
    get('local-size').value = '512x896';
    get('sampler').value = 'euler_a';
    get('count').value = '2';
    controls();
    report('已套用 R2 / R3 对照：每支 2 张，使用相同 seed，各自权重为 0.35。');
  };
  get('form').onsubmit = (event) => {
    event.preventDefault();
    const data = {mode: mode(), prompt: value('prompt'), count: numeric('count')};
    if (data.mode === 'local') {
      const [width, height] = value('local-size').split('x').map(Number);
      const loras = [value('lora')];
      if (value('compare') !== 'none') loras.push(value('compare'));
      if (loras.length === 2 && loras[0] === loras[1]) return report('请选择两项不同的配置进行对照。', true);
      Object.assign(data, {negative: value('negative'), loras, width, height, cfg: numeric('cfg'),
        steps: numeric('steps'), sampler: value('sampler'), weight: numeric('weight'),
        seed: value('seed') ? numeric('seed') : null});
    } else Object.assign(data, {model: value('model'), size: value('online-size'), quality: value('quality')});
    submitJob('/generate', data);
  };

  function setDatasetDownload(id) {
    const link = get('dataset-export');
    link.hidden = !id;
    if (id) link.href = `${api}/dataset/${encodeURIComponent(id)}.zip`;
  }
  get('train-dataset').onchange = () => {
    setDatasetDownload(value('train-dataset'));
    controls();
  };
  get('train-steps').onchange = () => {
    get('train-save').max = value('train-steps');
    if (numeric('train-save') > numeric('train-steps')) get('train-save').value = value('train-steps');
  };
  get('train-form').onsubmit = (event) => {
    event.preventDefault();
    submitJob('/train', {dataset: value('train-dataset'), name: value('train-name'), steps: numeric('train-steps'),
      save_every: numeric('train-save'), rank: numeric('train-rank'), lr: numeric('train-lr'),
      repeats: numeric('train-repeats'), seed: numeric('train-seed')});
  };

  function rowCount() {
    const count = [...sourceItems, ...addedImages.values()].filter((row) => row.selected).length;
    get('dataset-count').textContent = `${count} 张已选`;
    get('dataset-save').disabled = !count || datasetBusy;
  }

  function renderDataset() {
    const items = get('dataset-items');
    items.replaceChildren();
    for (const row of [...sourceItems, ...addedImages.values()]) {
      const box = document.createElement('div');
      box.className = 'dataset-item';
      const image = document.createElement('img');
      image.src = row.url;
      image.alt = '';
      image.loading = 'lazy';
      const select = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = row.selected;
      checkbox.onchange = () => { row.selected = checkbox.checked; rowCount(); };
      select.append(checkbox, document.createTextNode(row.name));
      const caption = document.createElement('input');
      caption.type = 'text';
      caption.value = row.caption;
      caption.maxLength = 1500;
      caption.placeholder = '填写人物特征、服装或构图标签';
      caption.setAttribute('aria-label', `${row.name} 的标注`);
      caption.oninput = () => { row.caption = caption.value; };
      box.append(image, select, caption);
      if (row.file) {
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = '移除';
        remove.setAttribute('aria-label', `移除上传图片 ${row.name}`);
        remove.onclick = () => {
          URL.revokeObjectURL(row.url);
          addedImages.delete(row.name);
          renderDataset();
        };
        box.append(remove);
      }
      items.append(box);
    }
    rowCount();
  }

  get('dataset-source').onchange = async () => {
    const last = ++sourceRequest;
    const source = value('dataset-source');
    sourceItems = [];
    renderDataset();
    if (!source) return;
    try {
      const result = await json(`/source/${encodeURIComponent(source)}`);
      if (last !== sourceRequest) return;
      sourceItems = result.items.map((item) => ({...item, selected: source !== 'reference'}));
      renderDataset();
    } catch (error) { if (last === sourceRequest) report(error.message, true); }
  };
  get('dataset-files').onchange = async (event) => {
    const files = [...event.target.files];
    event.target.value = '';
    const stem = (name) => name.replace(/\.[^.]+$/, '').toLowerCase();
    try {
      for (const file of files.filter((file) => /\.txt$/i.test(file.name))) {
        if (file.size > 8192) throw new Error(`${file.name} 超过 8 KiB。`);
        const caption = (await readText(file)).trim();
        if (caption.length > 1500) throw new Error(`${file.name} 超过 1500 字。`);
        uploadedCaptions.set(stem(file.name), caption);
        for (const row of addedImages.values()) {
          if (stem(row.name) === stem(file.name)) row.caption = caption;
        }
      }
    } catch (error) { report(error.message, true); }
    for (const file of files) {
      if (!imageExtensions.test(file.name) || addedImages.has(file.name)) continue;
      addedImages.set(file.name, {name: file.name, file, url: URL.createObjectURL(file),
        caption: uploadedCaptions.get(stem(file.name)) || '', selected: true});
    }
    renderDataset();
  };
  for (const [name, selected] of [['dataset-all', true], ['dataset-none', false]]) {
    get(name).onclick = () => {
      [...sourceItems, ...addedImages.values()].forEach((row) => { row.selected = selected; });
      renderDataset();
    };
  }
  get('dataset-form').onsubmit = async (event) => {
    event.preventDefault();
    const rows = [...sourceItems, ...addedImages.values()].filter((row) => row.selected);
    const data = {name: value('dataset-name'), trigger: value('dataset-trigger'), source: value('dataset-source')};
    if (rows.length > 150 || rows.reduce((total, row) => total + (row.file?.size || 0), 0) > 45 * 1048576) {
      return report('每个数据集最多 150 张，新上传图片合计不超过 45 MiB。', true);
    }
    datasetBusy = true;
    rowCount();
    try {
      data.items = await Promise.all(rows.map(async (row) => ({name: row.name, caption: row.caption,
        ...(row.file ? {data: await encode(row.file)} : {})})));
      const saved = await json('/datasets', data);
      await refresh();
      get('train-dataset').value = saved.id;
      get('train-dataset').onchange();
      report(`已保存数据集「${saved.name}」，共 ${saved.count} 张；已选为训练数据。`);
    } catch (error) { report(error.message, true); }
    finally { datasetBusy = false; rowCount(); }
  };

  get('dataset-restore').onchange = async (event) => {
    const input = event.currentTarget;
    const [file] = input.files;
    input.value = '';
    if (!file) return;
    if (file.size > 45 * 1048576) return report('ZIP 超过 45 MiB。可解压后用 --reference-dir 指向数据集，再通过 MCP 恢复。', true);
    input.disabled = true;
    try {
      const saved = await json('/datasets/restore', {data: await encode(file)});
      await refresh();
      get('train-dataset').value = saved.id;
      get('train-dataset').onchange();
      report(`已恢复数据集「${saved.name}」，共 ${saved.count} 张。`);
    } catch (error) { report(error.message, true); }
    finally { input.disabled = false; }
  };

  get('import-lora').onchange = async (event) => {
    const input = event.currentTarget;
    const [file] = input.files;
    input.value = '';
    if (!file) return;
    if (file.size > 45 * 1048576) return report('文件超过 45 MiB，请复制到工具箱的 loras 目录，再刷新环境。', true);
    input.disabled = true;
    try {
      const imported = await json('/loras', {name: file.name, data: await encode(file)});
      await refresh();
      get('lora').value = imported.id;
      get('lora').onchange();
      report(`已导入 ${file.name}。`);
    } catch (error) { report(error.message, true); }
    finally { input.disabled = false; }
  };

  controls();
  refresh().then(() => report('图像工作室已就绪。')).catch((error) => report(error.message, true));
})();
