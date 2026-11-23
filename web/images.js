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
  let exportingLora = false;
  let trainingRankTouched = false;
  let trainingStepsTouched = false;
  let sourceItems = [];
  let sourceRequest = 0;
  const addedImages = new Map();
  const uploadedCaptions = new Map();
  let lastGallery = '';
  let selectedPrompt = null;
  let backgroundPrompt = null;
  let backgroundSelection = null;
  let otherPrompt = null;
  let appliedDefaults = '';
  let localPluginEnabled = false;
  let pipelineModel = null;
  let backgroundInputsKey = '';
  const backgroundScene = field('background-generation-scene');
  const backgroundStatus = field('background-generation-status');
  const inBackgroundNode = () => !field('pipeline-stage').hidden
    && field('pipeline-stage').dataset.node === 'background-generation';

  function report(text, error = false) {
    for (const element of [get('status'), field('toolkit-status')]) {
      element.textContent = text;
      element.classList.toggle('error-text', error);
    }
    if (!field('assets').hidden && error) message(text, true);
  }


  async function json(path, data) {
    if (data !== undefined) {
      const payload = path.startsWith('/library/') ? data : {...data, ...projectScope()};
      return (await request(`${api}${path}`, payload)).json();
    }
    const scope = path === '/catalog' && openProject ? `?project_id=${encodeURIComponent(openProject.id)}` : '';
    const response = await fetch(`${api}${path}${scope}`);
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

  function selectedLoraFamily() {
    const lora = catalog?.loras.find((item) => item.id === value('lora'));
    if (lora) return lora.family;
    return value('base-model') || (catalog?.local?.training_supported && !catalog.local.supported ? 'sdxl' : 'sd1');
  }

  function updateCompareOptions() {
    if (!catalog) return;
    const family = selectedLoraFamily();
    const choices = catalog.loras.filter((lora) => lora.family === family)
      .map((lora) => new Option(lora.label, lora.id));
    retainOptions(get('compare'), [new Option('关闭', 'none'), new Option('底模', ''), ...choices]);
  }

  function updateGenerationDefaults() {
    const family = selectedLoraFamily();
    if (family === 'anima') {
      if (!['er_sde', 'euler'].includes(value('sampler'))) get('sampler').value = 'er_sde';
      if (value('steps') === '20') get('steps').value = '30';
      if (value('cfg') === '6.5') get('cfg').value = '4.5';
      if (value('local-size') === '512x896') get('local-size').value = '1024x1024';
      if (value('lora') && numeric('weight') === 0.35) get('weight').value = '1.0';
    } else {
      if (value('sampler') === 'er_sde') get('sampler').value = 'euler_a';
      if (value('steps') === '30') get('steps').value = '20';
      if (value('cfg') === '4.5') get('cfg').value = '6.5';
      if (value('local-size') === '1024x1024' && family === 'sd1') get('local-size').value = '512x896';
    }
  }

  function updateResolutionOptions(family) {
    const maxPixels = family === 'anima' ? 2359296 : family === 'sdxl' ? 1048576 : 655360;
    for (const option of get('local-size').options) {
      const [width, height] = option.value.split('x').map(Number);
      option.disabled = width * height > maxPixels;
    }
    if (get('local-size').selectedOptions[0]?.disabled) {
      get('local-size').value = family === 'anima' || family === 'sdxl' ? '1024x1024' : '512x896';
    }
  }

  function controls() {
    if (pipelineModel) setMode(pipelineModel.selected === 'models' ? 'local' : 'online');
    const local = mode() === 'local';
    const environment = catalog?.local;
    const working = Boolean(activeJob || submitting);
    const sdxl = environment?.training_models?.find((model) => model.id === 'sdxl');
    const anima = environment?.training_models?.find((model) => model.id === 'anima');
    const sd1 = environment?.training_models?.find((model) => model.id === 'sd1');
    const family = selectedLoraFamily();
    updateResolutionOptions(family);
    const sdxlReady = environment?.training_supported && environment?.training_device?.includes('MPS')
      && sdxl?.available && sdxl?.trainer_available;
    const animaReady = environment?.training_supported && anima?.available && anima?.trainer_available;
    const sd1MpsReady = environment?.training_supported && environment?.training_device?.includes('MPS')
      && sd1?.available && sd1?.trainer_available;
    const chosenOnline = effectiveNodeModel('image-node-model');
    const onlineProvider = providerCatalog.providers.find((provider) => provider.id === chosenOnline?.provider_id);
    const ready = local ? localPluginEnabled && (family === 'anima' ? animaReady : family === 'sdxl' ? sdxlReady
      : sd1MpsReady || environment?.supported && environment.engine && environment.model) : Boolean(onlineProvider);
    const installModel = environment?.training_models?.find((model) => model.id === value('install-base'));
    const trainModel = environment?.training_models?.find((model) => model.id === value('train-base'));
    get('local').hidden = get('local').disabled = !local;
    get('online').hidden = get('online').disabled = local;
    get('mode-note').textContent = local
      ? (family === 'anima'
        ? (animaReady ? '本地出图使用动漫专用 Anima Base、Apple MLX 与所选 LoRA。'
          : 'Anima 本地生成需要 Apple Silicon；请在工具箱安装 Anima Base 与 MFlux。')
        : family === 'sdxl'
        ? (sdxlReady ? '本地出图使用 Animagine XL 4.0、Apple MPS 和所选 SDXL LoRA。'
          : 'SDXL 本地预览需要 Apple Silicon，并安装 Animagine XL 底模和 MPS 训练环境。')
        : ready ? '本地出图使用当前底模和所选 LoRA。'
          : environment?.training_supported && !environment?.supported
            ? '本机可用 MPS 预览已安装的 SDXL LoRA；请选择 SDXL LoRA 或切换在线模型。'
            : '新环境请展开本地工具箱，拉取推理组件或完整 LoRA 工具链。')
      : (onlineProvider ? `将通过 ${onlineProvider.name} 调用 ${chosenOnline.model}。`
        : '请在此选择可用模型，或在「API Provider」设置全局默认图像模型。');
    get('generate').disabled = working || !ready || Boolean(pipelineModel && !pipelineModel.selected)
      || (inBackgroundNode() && (!backgroundPrompt || selectedPrompt !== backgroundPrompt));
    get('export-lora').disabled = exportingLora || !value('lora');
    get('use-existing-lora').disabled = !value('existing-lora-select');
    get('install').disabled = working || !environment?.supported || Boolean(environment.engine && environment.model);
    get('install-train').disabled = working || !environment?.training_supported
      || Boolean(installModel?.available && installModel?.trainer_available);
    get('train').disabled = working || !environment?.training_supported
      || !trainModel?.available || !trainModel?.trainer_available || !value('train-dataset');
    get('replay').disabled = working;
    const comparing = value('compare') !== 'none';
    get('count').max = local && comparing ? '2' : '4';
    if (numeric('count') > Number(get('count').max)) get('count').value = get('count').max;
    get('weight').disabled = !value('lora') || comparing;
  }

  function updateTrainingModelHelp() {
    const iris = value('train-base') === 'iris3b';
    const anima = value('train-base') === 'anima';
    get('train-repeats').closest('label').hidden = iris || anima;
    get('train-parent').closest('label').hidden = iris || anima;
    get('train-hint').textContent = anima
      ? 'Anima Base 使用 MFlux / Apple MLX，在 512 分辨率、Rank 16 下训练 25 个完整数据轮次，启用梯度检查点和 2 GB MLX 缓存。LoRA 权重兼容 ComfyUI；模型和衍生权重仅限非商业用途。'
      : iris
        ? 'Iris-3B Base 使用 Apple MLX，在 512 分辨率训练 Rank 16 LoRA，启用梯度检查点和 2 GB MLX 缓存。训练步数会取整为完整数据集轮次；预览图和 checkpoint 会保存在任务产物中。'
      : '基于所选底模独立训练。SD 1.x 使用 512 分辨率；SDXL 使用 1024 分辨率和节省内存的 U-Net-only 配置。Apple Silicon 可在本机用 MPS 预览 SDXL LoRA。';
    if (anima && !trainingStepsTouched) {
      const source = catalog?.sources.find((item) => item.id === value('train-dataset'));
      const updates = Math.max(1, source?.item_count || 77) * 25;
      get('train-steps').value = String(Math.min(5000, updates));
      get('train-save').value = String(Math.min(500, updates));
    }
  }

  function updateTrainingParentOptions() {
    if (!catalog) return;
    const family = value('train-base');
    const choices = catalog.loras.filter((lora) => lora.family === family)
      .map((lora) => new Option(lora.label, lora.id));
    retainOptions(get('train-parent'), [new Option('从头训练', ''), ...choices]);
  }

  function populateProjectConfigOptions() {
    if (!catalog) return;
    const trainingModels = catalog.local.training_models || [];
    const loras = catalog.loras.map(({label, id}) => [label, id]);
    const models = trainingModels.map(({label, id}) => [label, id]);
    const options = {
      'config-image-base': [['自动选择已安装底模', ''], ...models.filter(([, id]) => id !== 'iris3b')],
      'config-image-lora': [['底模', ''], ...loras],
      'config-image-compare': [['关闭对照', 'none'], ['底模', ''], ...loras],
      'config-train-base': [['自动选择已安装底模', ''], ...models],
      'config-train-dataset': [['每次手动选择', ''],
        ...catalog.sources.filter((source) => source.saved).map(({label, id}) => [label, id])],
      'config-train-parent': [['从头训练', ''], ...loras],
    };
    for (const [id, choices] of Object.entries(options)) {
      const select = field(id);
      const selected = select.value;
      if (!choices.some(([, value]) => value === selected)) {
        choices.push([`${selected} · 当前不可用`, selected]);
      }
      retainOptions(select, choices.map(([label, value]) => new Option(label, value)), selected);
    }
  }

  function applyProjectDefaults() {
    let config;
    try { config = projectConfig(); } catch { return; }
    const generation = config.generation;
    const training = config.training;
    const defaults = JSON.stringify([generation, training]);
    if (appliedDefaults === defaults) return;
    appliedDefaults = defaults;
    trainingRankTouched = true;
    trainingStepsTouched = true;
    const setValue = (id, value) => {
      const control = get(id);
      const text = String(value);
      if (!control.options || [...control.options].some((option) => option.value === text)) control.value = text;
    };
    const radios = get('form').querySelectorAll('[name="image-mode"]');
    if (!pipelineModel) for (const radio of radios) {
      radio.checked = radio.value === (localPluginEnabled ? generation.mode : 'online');
    }
    for (const [name, value] of [['base-model', generation.base_model], ['lora', generation.lora],
      ['weight', generation.weight], ['local-size', generation.size], ['seed', generation.seed ?? ''],
      ['sampler', generation.sampler], ['steps', generation.steps], ['cfg', generation.cfg],
      ['count', generation.count], ['negative', generation.negative], ['online-size', generation.online_size],
      ['quality', generation.quality]]) {
      setValue(name, value);
    }
    updateCompareOptions();
    setValue('compare', generation.compare);
    const trainValues = {'train-base': training.base_model, 'train-dataset': training.dataset,
      'train-name': training.name, 'train-steps': training.steps,
      'train-save': training.save_every, 'train-rank': training.rank, 'train-lr': training.lr,
      'train-repeats': training.repeats, 'train-seed': training.seed};
    for (const [id, value] of Object.entries(trainValues)) setValue(id, value);
    updateTrainingParentOptions();
    setValue('train-parent', training.parent);
    updateTrainingModelHelp();
    controls();
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
    const previousFamily = selectedLoraFamily();
    const project = openProject?.id;
    const loaded = await json('/catalog');
    if (project !== openProject?.id) return;
    catalog = loaded;
    const env = catalog.local;
    const models = env.training_models || [];
    const options = catalog.loras.filter((lora) => ['sd1', 'sdxl', 'anima'].includes(lora.family))
      .map((lora) => new Option(lora.label, lora.id));
    retainOptions(get('dataset-source'), [new Option('仅使用上传图片', ''), ...catalog.sources.map((s) => new Option(s.label, s.id))]);
    retainOptions(get('train-dataset'), [new Option('先保存数据集', ''), ...catalog.sources.filter((s) => s.saved).map((s) => new Option(s.label, s.id))]);
    retainOptions(get('install-base'), models.map((model) => new Option(model.label, model.id)),
      first && env.training_supported && !env.supported ? 'anima' : undefined);
    const installedModels = models.filter((model) => model.available);
    const generationModels = models.filter((model) => ['sd1', 'sdxl', 'anima'].includes(model.id)
      && model.available && model.trainer_available);
    retainOptions(get('base-model'), generationModels.map((model) => new Option(model.label, model.id)),
      first ? (generationModels.some((model) => model.id === 'anima') ? 'anima'
        : generationModels.some((model) => model.id === 'sdxl') ? 'sdxl' : generationModels[0]?.id) : undefined);
    retainOptions(get('lora'), [new Option('底模', ''), ...options]);
    retainOptions(get('existing-lora-select'), [new Option('选择已有 LoRA', ''),
      ...catalog.loras.filter((lora) => ['sd1', 'sdxl', 'anima'].includes(lora.family))
        .map((lora) => new Option(`${lora.label} · ${lora.family}`, lora.id))]);
    const selectedLora = catalog.loras.find((item) => item.id === value('lora'));
    if (selectedLora && [...get('base-model').options].some((option) => option.value === selectedLora.family)) {
      get('base-model').value = selectedLora.family;
    }
    updateCompareOptions();
    if (first || selectedLoraFamily() !== previousFamily) updateGenerationDefaults();
    retainOptions(get('train-base'), installedModels.length
      ? installedModels.map((model) => new Option(model.label, model.id))
      : [new Option('先安装训练底模', '')], installedModels.some((model) => model.id === value('train-base'))
        ? undefined : installedModels[0]?.id);
    updateTrainingParentOptions();
    if (!trainingRankTouched) get('train-rank').value = ['iris3b', 'anima'].includes(value('train-base')) ? '16'
      : value('train-base') === 'sdxl' ? '8' : '4';
    updateTrainingModelHelp();
    populateProjectConfigOptions();
    applyProjectDefaults();
    get('r2r3').hidden = !['r2-200', 'r3-200'].every((id) => catalog.loras.some((lora) => lora.id === id));

    const status = (path, supported) => path || (supported ? '尚未安装' : '当前设备不支持');
    const sdxlModel = models.find((model) => model.id === 'sdxl');
    const animaModel = models.find((model) => model.id === 'anima');
    const inferenceStatus = env.supported ? status(env.engine, true)
      : animaModel?.available && animaModel?.trainer_available
        ? 'Anima Base · MFlux / Apple MLX'
      : env.training_supported && env.training_device?.includes('MPS')
        && sdxlModel?.available && sdxlModel?.trainer_available
        ? 'Animagine XL · Diffusers / MPS' : env.training_supported ? '此设备未配置本地推理后端' : '仅支持 Windows x64';
    const modelStatus = installedModels.map((model) => model.label).join(' · ')
      || status('', env.training_supported);
    const info = [['推理工具', inferenceStatus], ['训练底模', modelStatus],
      ['LoRA 训练环境', status(env.trainer, env.training_supported)],
      ['训练设备', env.training_device || '当前设备不支持'],
      ['参考目录', env.reference || 'Reference/Img'], ['工作目录', env.workspace]];
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
    phase.textContent = job ? [job.phase, job.error || job.note,
      job.kind === 'train' ? job.options.dataset_name : null,
      job.kind === 'train' ? job.options.base_model_name : null].filter(Boolean).join(' · ')
      : '生成或训练完成后，可在这里查看产物。';
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
    const logBox = get('log-box');
    logBox.hidden = !job;
    if (job && logBox.dataset.job !== job.id) {
      logBox.dataset.job = job.id;
      logBox.open = true;
    }
    const modelRequest = job?.provider_request;
    const requestElapsed = modelRequest && activeState(job) && modelRequest.phase !== 'received'
      ? Math.max(modelRequest.elapsed, (Date.now() - Date.parse(job.started)) / 1000) : modelRequest?.elapsed;
    const log = job ? [
      `任务 ${job.id} · ${job.options.model || job.options.base_model || '本地工具'}`,
      modelRequest && [
        `${modelRequest.provider} / ${modelRequest.model}`,
        modelRequest.status ? 'HTTP ' + modelRequest.status : '',
        Number.isFinite(requestElapsed) ? `请求耗时 ${requestElapsed.toFixed(1)} 秒` : '',
        `输入 ${modelRequest.prompt_chars.toLocaleString()} 字符`,
        modelRequest.response_bytes != null ? `响应 ${modelRequest.response_bytes.toLocaleString()} 字节` : '',
        ...tokenSummary(modelRequest.usage),
      ].filter(Boolean).join(' · '),
      `[${job.started}] 已提交`, job.log, job.ended && `[${job.ended}] ${job.phase}${job.error ? ' · ' + job.error : ''}`,
      job.state === 'done' && job.kind === 'generate' ? `已保存 ${job.images.length} 张候选图` : '',
    ].filter(Boolean).join('\n') : '';
    if (get('log').textContent !== log) get('log').textContent = log;

    const pending = Boolean(activeState(job));
    const completed = job?.state === 'done';
    reportNodeOutput('generation', job?.kind === 'generate' ? {
      pending, fromJob: true, label: completed ? '已完成的生成任务' : '',
      reference: completed ? {kind: 'job', id: job.id} : null,
    } : null);
    const background = job?.kind === 'generate' && (job.options.pipeline_node === 'background-generation'
      || !job.options.pipeline_node && job.options.scene_design_id);
    if (background) {
      const chosen = backgroundSelection?.job === job.id ? backgroundSelection.name
        : job.images?.length === 1 ? job.images[0].name : '';
      reportNodeOutput('background-generation', {
        pending, fromJob: true, reference: completed && chosen ? {kind: 'artifact', id: `${job.id}/images/${chosen}`} : null,
      });
    }
    reportNodeOutput('candidates', job?.kind === 'generate' ? {
      fromJob: true, label: completed && job.images?.length ? `${job.images.length} 张候选图` : '',
      reference: completed && job.images?.length === 1
        ? {kind: 'artifact', id: `${job.id}/images/${job.images[0].name}`} : null,
    } : null);
    reportNodeOutput('train', job?.kind === 'train' ? {
      pending, fromJob: true, label: completed ? '训练已完成，可选择检查点' : '',
      reference: completed && job.checkpoints?.length === 1 && job.checkpoints[0].id
        ? {kind: 'lora', id: job.checkpoints[0].id} : null,
    } : null);

    const canUse = (item) => catalog?.loras.some((lora) => lora.id === item.id
      && (lora.family !== 'sdxl' || (catalog.local?.training_device?.includes('MPS')
        && catalog.local?.training_models?.some((model) => model.id === 'sdxl'
          && model.available && model.trainer_available))));
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
        const scope = projectScope();
        try {
          const saved = await registerMedia(name, {artifact: {job_id: job.id, name: item.name}}, scope);
          if (scope.project_id !== openProject?.id) return;
          useManagedMedia(name, saved.id);
          renderFiles();
          if (['show', 'background'].includes(action.value)) asset.value = name;
          report('图片已加入项目素材，可在「演出」里绑定到台词。');
        } catch (error) {
          report(error.message, true);
        } finally { add.disabled = false; }
      };
      const save = document.createElement('a');
      save.href = url;
      save.download = name;
      save.textContent = '下载';
      actions.append(add, save);
      if (background) {
        const choose = document.createElement('button');
        choose.type = 'button';
        choose.textContent = '选为背景成果';
        choose.onclick = () => {
          backgroundSelection = {job: job.id, name: item.name};
          reportNodeOutput('background-generation', {reference: {kind: 'artifact', id: `${job.id}/images/${item.name}`}});
          report('已选定背景候选图，可在背景生成节点通过。');
        };
        actions.append(choose);
      }
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
      if (use.disabled) use.title = '此设备尚未安装可用于该 LoRA 的本地预览环境。';
      use.onclick = () => prepareLora(item.id);
      const row = document.createElement('div');
      row.className = 'studio-actions checkpoint-row';
      row.append(file, use);
      get('checkpoints').append(row);
    }
  }

  function setMode(value) {
    get('form').querySelector(`[name="image-mode"][value="${value}"]`).checked = true;
  }

  async function prepareLora(id) {
    if (!id) return;
    const project = openProject?.id;
    try {
      const selection = await json('/loras/prepare', {id});
      if (openProject?.id !== project) return;
      if (!catalog?.loras.some((item) => item.id === id)) await refresh();
      if (openProject?.id !== project) return;
      if (![...get('lora').options].some((option) => option.value === id)) {
        throw new Error('当前生成界面无法选择该权重，请刷新模型列表。');
      }
      goWorkspace('stage-generation');
      document.dispatchEvent(new CustomEvent('prisma-select-input', {
        detail: {node: 'generation', port: 'model', source: 'models'},
      }));
      get('lora').value = id;
      get('lora').onchange();
      get('compare').value = 'none';
      get('weight').value = selection.generation.weight;
      controls();
      get('prompt').focus();
      report(selection.ready ? `已加载「${selection.label}」，可以填写提示词并生成。`
        : `已选中「${selection.label}」。${selection.hint}`);
    } catch (error) { message(error.message, true); report(error.message, true); }
  }

  get('existing-lora-select').onchange = () => {
    controls();
    const stage = field('pipeline-stage');
    if (stage.hidden || !['models', 'train'].includes(stage.dataset.node)) return;
    const lora = catalog?.loras.find((item) => item.id === value('existing-lora-select'));
    reportNodeOutput(stage.dataset.node, lora ? {reference: {kind: 'lora', id: lora.id}, label: lora.label} : null);
  };
  get('use-existing-lora').onclick = () => prepareLora(value('existing-lora-select'));
  get('refresh-loras').onclick = (event) => run(event.currentTarget, '正在刷新权重列表…', async () => {
    await json('/library/refresh', {});
    await refresh();
    report('已刷新可用的 LoRA。');
  });
  document.addEventListener('prisma-use-lora', (event) => prepareLora(event.detail.id));

  function reuse(options) {
    if (options.mode === 'local' && options.loras.some((x) => x.id && !catalog.loras.some((lora) => lora.id === x.id))) {
      return report('该任务使用的 LoRA 已不可用。请重新导入权重。', true);
    }
    setMode(options.mode);
    selectedPrompt = options.prompt_id || null;
    const values = {prompt: options.prompt, count: options.count};
    if (options.mode === 'local') {
      const size = `${options.width}x${options.height}`;
      if (![...get('local-size').options].some((option) => option.value === size)) {
        get('local-size').add(new Option(size, size));
      }
      Object.assign(values, {negative: options.negative, seed: options.seed, steps: options.steps, cfg: options.cfg,
        sampler: options.sampler, 'local-size': size, lora: options.loras[0].id,
        compare: options.loras[1]?.id ?? 'none', weight: options.loras[0].weight ?? 0.35});
    } else {
      Object.assign(values, {'online-size': options.size, quality: options.quality});
    }
    for (const [id, setting] of Object.entries(values)) get(id).value = setting;
    controls();
    report('已恢复本次任务的生成参数。');
  }

  async function show(id) {
    if (!id) return renderJob(null);
    const project = openProject?.id;
    const job = await json(`/jobs/${id}`);
    if (project === openProject?.id && currentJob === id) renderJob(job);
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
    const background = route === '/generate' && inBackgroundNode();
    const node = route === '/generate' ? 'generation' : route === '/train' ? 'train' : null;
    const project = openProject?.id;
    if (background) reportNodeOutput('background-generation', {pending: true});
    else if (node) reportNodeOutput(node, {pending: true});
    controls();
    try {
      const job = await json(route, data);
      if (project !== openProject?.id) return;
      activeJob = activeState(job) ? job.id : null;
      currentJob = job.id;
      report('任务已提交。');
      await refresh(job.id);
    } catch (error) {
      if (node && project === openProject?.id && !activeJob) reportNodeOutput(node, null);
      if (background && project === openProject?.id && !activeJob) reportNodeOutput('background-generation', null);
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
  get('install-base').onchange = controls;
  get('train-base').onchange = () => {
    if (!trainingRankTouched) get('train-rank').value = ['iris3b', 'anima'].includes(value('train-base')) ? '16'
      : value('train-base') === 'sdxl' ? '8' : '4';
    updateTrainingParentOptions();
    updateTrainingModelHelp();
    controls();
  };
  get('train-rank').oninput = () => { trainingRankTouched = true; };
  get('install-train').onclick = () => submitJob('/install', {target: 'training', base_model: value('install-base')});
  get('form').querySelectorAll('[name="image-mode"]').forEach((input) => input.onchange = controls);
  field('image-node-model').addEventListener('change', controls);
  get('compare').onchange = controls;
  get('export-lora').onclick = async () => {
    const lora = value('lora');
    if (!lora || exportingLora) return;
    exportingLora = true;
    controls();
    report('正在导出 LoRA 与训练资料…');
    try {
      const exported = await json('/loras/export', {loras: [lora]});
      const download = document.createElement('a');
      download.href = exported.url;
      download.download = 'prisma-lora.zip';
      download.click();
      report('LoRA 迁移 ZIP 已生成。');
    } catch (error) { report(error.message, true); }
    finally {
      exportingLora = false;
      controls();
    }
  };
  get('lora').onchange = () => {
    get('weight').value = catalog?.loras.find((x) => x.id === value('lora'))?.weight ?? 0.35;
    const family = selectedLoraFamily();
    if (family !== value('base-model') && [...get('base-model').options].some((option) => option.value === family)) {
      get('base-model').value = family;
    }
    updateCompareOptions();
    updateGenerationDefaults();
    controls();
  };
  get('base-model').onchange = () => {
    updateCompareOptions();
    updateGenerationDefaults();
    controls();
  };
  get('preset').onchange = () => {
    const prompt = presets[value('preset')];
    if (prompt) {
      get('prompt').value = `${mode() === 'local' ? 'prismaCel, ' : ''}${prompt}`;
      selectedPrompt = null;
    }
    get('preset').value = '';
  };
  get('scene').onclick = (event) => run(event.currentTarget, '正在带入场景…', async () => {
    try {
      const project = openProject?.id;
      const approved = await approvedNodeInput('scenario');
      if (project !== openProject?.id) return;
      const document = approved?.scenario || scenarioData();
      const available = document?.chapters.flatMap((chapter) => chapter.scenes) || [];
      const selected = inspectedText === scenario.value && scenes[Number(sceneSelect.value)];
      const scene = available.find((item) => item.id === selected?.id) || available[0];
      if (!scene) return report('请先生成或导入 Scenario。', true);
      const kind = action.value === 'background' ? 'background' : action.value === 'show' ? 'sprite' : 'cg';
      const line = selectedLine?.scene === scene.id && selectedLine.index <= scene.lines.length ? selectedLine.index : 1;
      const response = await request('/api/asset-prompt', {scenario: document, scene: scene.id, kind,
        line, brief: value('prompt').trim().slice(0, 3000), config: projectConfig(), ...projectScope()});
      const result = await response.json();
      if (project !== openProject?.id) return;
      selectedPrompt = result.prompt_id;
      const context = result.context.map((line) => `${line.speaker ? `${line.speaker}：` : ''}${line.text}`).join('\n').slice(0, 700);
      get('prompt').value = `${result.prompt}\n\n场景参考（${result.chapter} · ${result.scene}）：\n${context}`.slice(0, 4000);
      get('prompt').dispatchEvent(new Event('input', {bubbles: true}));
      get('prompt').focus();
      report('已带入场景和素材构图建议。');
    } catch (error) { report(error.message, true); }
  });
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
    if (inBackgroundNode() && (!backgroundPrompt || selectedPrompt !== backgroundPrompt)) {
      return report('请先载入一个场景的背景提示词。', true);
    }
    const data = {mode: mode(), prompt: value('prompt'), count: numeric('count')};
    if (selectedPrompt) data.prompt_id = selectedPrompt;
    if (openProject) {
      data.project_id = openProject.id;
      data.pipeline_node = inBackgroundNode() ? 'background-generation' : 'generation';
    }
    if (data.mode === 'local') {
      const [width, height] = value('local-size').split('x').map(Number);
      const loras = [value('lora')];
      if (value('compare') !== 'none') loras.push(value('compare'));
      if (loras.length === 2 && loras[0] === loras[1]) return report('请选择两项不同的配置进行对照。', true);
      Object.assign(data, {negative: value('negative'), loras, base_model: selectedLoraFamily(), width, height, cfg: numeric('cfg'),
        steps: numeric('steps'), sampler: value('sampler'), weight: numeric('weight'),
        seed: value('seed') ? numeric('seed') : null});
    } else Object.assign(data, {
      model_override: readProviderRef(field('image-node-model').value),
      size: value('online-size'), quality: value('quality')});
    submitJob('/generate', data);
  };

  function setDatasetDownload(id) {
    const link = get('dataset-export');
    link.hidden = !id;
    if (id) link.href = `${api}/dataset/${encodeURIComponent(id)}.zip`;
  }
  get('train-dataset').onchange = () => {
    setDatasetDownload(value('train-dataset'));
    updateTrainingModelHelp();
    controls();
  };
  get('train-steps').oninput = () => { trainingStepsTouched = true; };
  get('train-steps').onchange = () => {
    get('train-save').max = value('train-steps');
    if (numeric('train-save') > numeric('train-steps')) get('train-save').value = value('train-steps');
  };
  get('train-form').onsubmit = (event) => {
    event.preventDefault();
    const data = {dataset: value('train-dataset'), name: value('train-name'), base_model: value('train-base'),
      steps: numeric('train-steps'),
      save_every: numeric('train-save'), rank: numeric('train-rank'), lr: numeric('train-lr'),
      repeats: numeric('train-repeats'), seed: numeric('train-seed')};
    if (value('train-parent')) data.parent_lora = value('train-parent');
    submitJob('/train', data);
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
      reportNodeOutput('dataset', {reference: {kind: 'dataset', id: saved.id}, label: saved.name});
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
      reportNodeOutput('dataset', {reference: {kind: 'dataset', id: saved.id}, label: saved.name});
      report(`已恢复数据集「${saved.name}」，共 ${saved.count} 张。`);
    } catch (error) { report(error.message, true); }
    finally { input.disabled = false; }
  };

  get('import-lora').onchange = async (event) => {
    const input = event.currentTarget;
    const [file] = input.files;
    input.value = '';
    if (!file) return;
    const scope = projectScope();
    const family = value('import-family');
    if (file.size > 45 * 1048576) return report('文件超过 45 MiB 的浏览器上传上限。', true);
    input.disabled = true;
    try {
      const data = await encode(file);
      if (scope.project_id !== openProject?.id) return;
      const response = await request(`${api}/loras`, {name: file.name, family, data, ...scope});
      const imported = await response.json();
      if (scope.project_id !== openProject?.id) return;
      await refresh();
      if (scope.project_id !== openProject?.id) return;
      get('existing-lora-select').value = imported.id;
      get('existing-lora-select').onchange();
      report(`已导入 ${file.name}。点「用于本地生成」加载此权重。`);
    } catch (error) { report(error.message, true); }
    finally { input.disabled = false; }
  };

  const libraryLabels = {project: '项目', revision: '项目版本', source: '原文', scenario: 'Scenario',
    cues: '演出绑定', prompt: '提示词', script: '脚本', asset: '媒体素材', tool: '工具', model: '模型', lora: 'LoRA',
    dataset: '数据集', job: '任务', artifact: '产物', export: '导出', pipeline: '项目节点状态',
    'node-version': '节点版本', handoff: '历史节点版本'};
  const libraryStates = {...stateLabels, installed: '已安装', not_installed: '未安装', remote: '在线', legacy: '历史数据'};
  const relationLabels = {current_handoff: '历史当前版本', previous_handoff: '上一节点版本',
    from_handoff: '历史上游版本', current_node_version: '当前节点版本', previous_node_version: '上一节点版本',
    from_node_version: '使用的上游版本', uses_resource: '关联产物',
    uses_model: '使用底模', uses_dataset: '使用数据集', uses_lora: '使用 LoRA',
    continues_from: '续训起点', replay_of: '复跑来源', produced_by: '来源任务', weight_file: '权重文件',
    derived_from: '数据集来源', includes: '包含', uses_prompt: '使用提示词', for_project: '所属项目',
    uses_source: '使用原文', uses_scenario: '使用 Scenario', uses_cues: '使用演出绑定', uses_asset: '使用素材', from_script: '已通过的脚本',
    from_source: '原文来源', from_scenario: 'Scenario 来源', adapted_from: '改写自',
    from_artifact: '来自产物', current_revision: '当前版本', previous_revision: '上一版',
    for_scenario: '对应 Scenario', member_of: '所属数据集', uses_tool: '使用工具'};
  const libraryPage = 20;
  let libraryOffset = 0;
  let libraryRequest = 0;

  function libraryNode(tag, label) {
    const node = document.createElement(tag);
    node.textContent = label;
    return node;
  }

  function libraryButton(label, action) {
    const button = libraryNode('button', label);
    button.type = 'button';
    button.onclick = action;
    return button;
  }

  async function libraryDetail(kind, id) {
    const detail = get('library-detail');
    const generation = ++libraryRequest;
    detail.hidden = false;
    detail.replaceChildren(libraryNode('p', '正在读取…'));
    try {
      const item = await json('/library/item', {kind, id});
      if (generation !== libraryRequest) return;
      detail.replaceChildren(libraryNode('h3', item.label), libraryNode('p', [
        libraryLabels[item.kind], item.id, item.path, item.available === false ? '文件缺失' : null,
      ].filter(Boolean).join(' · ')));
      if (item.available && item.metadata.url?.startsWith('/api/')) {
        const link = libraryNode('a', item.kind === 'artifact' ? '打开产物' : '下载');
        link.href = item.metadata.url;
        detail.append(link);
      }
      if (item.kind === 'lora' && item.available && ['sd1', 'sdxl', 'anima'].includes(item.metadata.family)) {
        detail.append(libraryButton('用于本地生成', () => prepareLora(item.id)));
      }
      if (item.kind === 'job' && item.available) {
        detail.append(libraryButton('查看任务和产物', async () => {
          goWorkspace('stage-candidates');
          currentJob = item.id;
          const history = get('history');
          if (![...history.options].some((option) => option.value === item.id)) {
            history.add(new Option(item.label, item.id));
          }
          history.value = item.id;
          try { await show(item.id); history.focus(); }
          catch (error) { report(error.message, true); }
        }));
      }
      if ((item.kind === 'project' || item.kind === 'revision') && item.available) {
        detail.append(libraryButton('打开到工作台', async () => {
          if (!allowProjectSwitch()) return;
          try {
            await loadProject(item.kind === 'project' ? item.id : item.metadata.project_id,
              item.kind === 'revision' ? item.id : undefined);
          } catch (error) { report(error.message, true); }
        }));
      }
      if (item.kind === 'prompt' && item.metadata.prompt) {
        detail.append(libraryButton('带入生成表单', () => {
          goWorkspace('stage-generation');
          get('prompt').value = item.metadata.prompt;
          get('negative').value = item.metadata.negative || '';
          selectedPrompt = item.id;
          get('prompt').focus();
        }));
      }
      if (item.kind === 'asset' && item.available) {
        detail.append(libraryButton('加入当前项目素材', async () => {
          const scope = projectScope();
          try {
            const matchingScope = (item.project_id ?? null) === (openProject?.id ?? null);
            const saved = matchingScope && item.metadata.url?.startsWith('/api/workspace/assets/')
              ? {id: item.id, name: item.metadata.name}
              : await registerMedia(item.label, {library_id: item.id}, scope);
            if (scope.project_id !== openProject?.id) return;
            if (uploads.has(saved.name) && !confirm(`替换同名素材「${saved.name}」？`)) return;
            useManagedMedia(saved.name, saved.id);
            renderFiles();
            previewProjectAsset(saved.name);
            message('素材已加入当前项目。');
          } catch (error) { report(error.message, true); }
        }));
      }
      if (item.kind === 'scenario' && item.metadata.scenario) {
        detail.append(libraryButton('载入剧本', () => {
          if (scenario.value && !confirm('替换当前剧本？')) return;
          showScenario(item.metadata.scenario);
          goWorkspace('stage-scenario');
        }));
      }
      const editor = {source: raw, cues}[item.kind];
      const content = item.kind === 'cues' && item.metadata.cues
        ? JSON.stringify(item.metadata.cues, null, 2) : item.metadata.text;
      if (editor && typeof content === 'string') {
        detail.append(libraryButton('载入编辑器', () => {
          if (editor.value.trim() && editor.value !== content && !confirm('替换编辑框中的文本？')) return;
          editor.value = content;
          editor.dispatchEvent(new Event('input'));
          goWorkspace(editor === raw ? 'stage-raw' : 'stage-cues');
          editor.focus();
        }));
      }
      if (item.links.length) {
        detail.append(libraryNode('h4', '关联记录'));
        const links = libraryNode('ul', '');
        links.className = 'library-rows';
        for (const link of item.links) {
          const title = `${link.direction === 'in' ? '引用此记录' : relationLabels[link.relation] || link.relation} · ${libraryLabels[link.kind]} · ${link.label || link.id}`;
          const row = libraryNode('li', '');
          row.append(link.label ? libraryButton(title, () => libraryDetail(link.kind, link.id)) : libraryNode('span', title));
          links.append(row);
        }
        detail.append(links);
      }
      const metadata = libraryNode('details', '');
      metadata.append(libraryNode('summary', '记录参数'), libraryNode('pre', JSON.stringify(item.metadata, null, 2)));
      detail.append(metadata);
    } catch (error) {
      if (generation === libraryRequest) detail.replaceChildren(libraryNode('p', error.message));
    }
  }

  async function loadLibrary(offset = 0) {
    const generation = ++libraryRequest;
    const status = get('library-status');
    get('library-detail').hidden = true;
    get('library-rows').replaceChildren();
    get('library-prev').disabled = true;
    get('library-next').disabled = true;
    status.textContent = '正在查询…';
    const filters = {query: value('library-query').trim(), offset, limit: libraryPage};
    if (value('library-scope') === 'current' && openProject) filters.project_id = openProject.id;
    else filters.shared = true;
    if (value('library-kind')) filters.kind = value('library-kind');
    try {
      const result = await json('/library/list', filters);
      if (generation !== libraryRequest) return;
      libraryOffset = offset;
      status.textContent = result.items.length ? `${offset + 1}–${offset + result.items.length} / ${result.total} 条` : '没有匹配的记录';
      get('library-prev').disabled = offset === 0;
      get('library-next').disabled = offset + libraryPage >= result.total;
      get('library-rows').replaceChildren(...result.items.map((item) => {
        const row = libraryNode('li', '');
        const state = item.available === false ? '文件缺失' : libraryStates[item.state];
        const title = [libraryLabels[item.kind], item.label, state].filter(Boolean).join(' · ');
        row.append(libraryButton(title, () => libraryDetail(item.kind, item.id)));
        return row;
      }));
    } catch (error) {
      if (generation === libraryRequest) status.textContent = error.message;
    }
  }

  document.addEventListener('prisma-library-open', (event) => {
    const {kind, id} = event.detail;
    libraryDetail(kind, id);
    get('library-detail').scrollIntoView({block: 'start'});
  });
  async function usePassedPrompt(reference, {onlyEmpty = false} = {}) {
    const project = openProject?.id;
    if (onlyEmpty && value('prompt').trim()) return;
    try {
      const response = await request('/api/images/library/item', {kind: 'prompt', id: reference.id});
      const {metadata} = await response.json();
      const stage = field('pipeline-stage');
      if (openProject?.id !== project || stage.hidden || stage.dataset.node !== 'generation') return;
      if (onlyEmpty && value('prompt').trim()) return;
      get('prompt').value = metadata.prompt;
      get('negative').value = metadata.negative || '';
      selectedPrompt = reference.id;
      get('prompt').dispatchEvent(new Event('input', {bubbles: true}));
    } catch (error) { report(error.message, true); }
  }
  document.addEventListener('prisma-node-use-prompt', (event) => usePassedPrompt(event.detail));
  backgroundScene.onchange = async () => {
    const project = openProject?.id;
    const id = backgroundScene.value;
    backgroundPrompt = null;
    selectedPrompt = null;
    get('prompt').value = '';
    controls();
    if (!id) {
      backgroundStatus.textContent = '「场景背景」还没有可用的提示词。';
      return;
    }
    backgroundStatus.textContent = '正在载入提示词…';
    try {
      const response = await request('/api/scenario/background-prompt', {project_id: project, id});
      const result = await response.json();
      if (openProject?.id !== project || !inBackgroundNode() || backgroundScene.value !== id) return;
      backgroundPrompt = selectedPrompt = result.prompt_id;
      get('prompt').value = result.prompt;
      get('negative').value = result.negative;
      backgroundStatus.textContent = '';
    } catch (error) {
      if (openProject?.id === project && inBackgroundNode() && backgroundScene.value === id) {
        backgroundStatus.textContent = error.message;
      }
    } finally { controls(); }
  };
  async function loadBackgroundScenes() {
    const project = openProject?.id;
    const previous = backgroundScene.value;
    const approved = nodeInputs.filter((item) => item.kind === 'scene-design');
    backgroundScene.replaceChildren();
    backgroundScene.disabled = true;
    backgroundPrompt = null;
    selectedPrompt = null;
    get('prompt').value = '';
    backgroundStatus.textContent = '正在读取场景…';
    controls();
    try {
      if (openProject?.id !== project || !inBackgroundNode()) return;
      const scenes = approved;
      backgroundScene.replaceChildren(...scenes.map((item, index) => new Option(`${index + 1} · ${item.label}`, item.id)));
      backgroundScene.disabled = !scenes.length;
      if (scenes.some((item) => item.id === previous)) backgroundScene.value = previous;
      await backgroundScene.onchange();
    } catch (error) {
      if (openProject?.id === project && inBackgroundNode()) backgroundStatus.textContent = error.message;
    }
  }
  async function usePassedDataset(reference) {
    const project = openProject?.id;
    try {
      if (![...get('train-dataset').options].some((option) => option.value === reference.id)) await refresh();
      if (project !== openProject?.id || field('pipeline-stage').hidden || field('pipeline-stage').dataset.node !== 'train') return;
      if ([...get('train-dataset').options].some((option) => option.value === reference.id)) {
        get('train-dataset').value = reference.id;
        get('train-dataset').onchange();
      }
    } catch (error) { report(error.message, true); }
  }
  document.addEventListener('prisma-node-use-dataset', (event) => usePassedDataset(event.detail));
  document.addEventListener('prisma-node-use-lora', ({detail}) => {
    if ([...get('existing-lora-select').options].some((option) => option.value === detail.id)) {
      get('existing-lora-select').value = detail.id;
      get('existing-lora-select').onchange();
    } else report('该 LoRA 当前不可用。请刷新权重列表。', true);
  });
  document.addEventListener('prisma-node-tools', ({detail}) => {
    if (catalog && ['generation', 'background-generation', 'train'].includes(detail)) applyProjectDefaults();
    if (detail === 'generation') {
      const ref = nodeInputs.find((item) => item.kind === 'prompt');
      if (ref) usePassedPrompt(ref, {onlyEmpty: true});
    }
    if (detail === 'candidates') {
      const ref = nodeInputs.find((item) => item.kind === 'job');
      if (ref && [...get('history').options].some((option) => option.value === ref.id)) {
        get('history').value = ref.id;
        get('history').onchange();
      }
    }
    if (detail === 'train' && !value('train-dataset')) {
      const ref = nodeInputs.find((item) => item.kind === 'dataset');
      if (ref) usePassedDataset(ref);
    }
    if (field('stage-tools').contains(get('library'))) loadLibrary();
  });
  document.addEventListener('prisma-plugins-change', ({detail}) => {
    localPluginEnabled = detail.plugins.some((plugin) => plugin.id === 'local-image' && plugin.enabled);
    const localRadio = get('form').querySelector('[name="image-mode"][value="local"]');
    localRadio.disabled = !localPluginEnabled;
    field('config-image-mode').querySelector('option[value="local"]').disabled = !localPluginEnabled;
    field('settings-training').hidden = !localPluginEnabled;
    document.querySelector('.config-nav a[href="#settings-training"]').hidden = !localPluginEnabled;
    if (!localPluginEnabled) setMode('online');
    controls();
  });
  document.addEventListener('prisma-node-inputs', ({detail}) => {
    pipelineModel = ['generation', 'background-generation'].includes(detail.node)
      ? detail.ports.find((port) => port.id === 'model') : null;
    get('form').querySelector('.mode-switch').hidden = Boolean(pipelineModel);
    if (detail.node === 'background-generation' && detail.ports.every((port) => !port.required || port.selected)) {
      const key = `${openProject?.id}/${nodeInputs.filter((item) => item.kind === 'scene-design').map((item) => item.id).join(',')}`;
      if (backgroundInputsKey !== key) {
        backgroundInputsKey = key;
        otherPrompt ??= {id: selectedPrompt, text: value('prompt'), negative: value('negative')};
        loadBackgroundScenes();
      }
    }
    controls();
  });
  document.addEventListener('prisma-before-view-change', ({detail}) => {
    if (!['generation', 'background-generation'].includes(detail)) {
      pipelineModel = null;
      get('form').querySelector('.mode-switch').hidden = false;
    }
    if (detail !== 'background-generation') backgroundInputsKey = '';
    if (detail === 'background-generation' || !otherPrompt) return;
    selectedPrompt = otherPrompt.id;
    get('prompt').value = otherPrompt.text;
    get('negative').value = otherPrompt.negative;
    otherPrompt = backgroundPrompt = null;
    queueMicrotask(controls);
  });
  document.addEventListener('prisma-view-change', (event) => {
    if (event.detail === 'assets') loadLibrary();
    if (['images', 'models'].includes(event.detail)) applyProjectDefaults();
  });
  get('library-search').onsubmit = (event) => { event.preventDefault(); loadLibrary(); };
  get('library-kind').onchange = () => loadLibrary();
  get('library-scope').onchange = () => loadLibrary();
  get('library-prev').onclick = () => loadLibrary(Math.max(0, libraryOffset - libraryPage));
  get('library-next').onclick = () => loadLibrary(libraryOffset + libraryPage);
  get('library-sync').onclick = async () => {
    const button = get('library-sync');
    button.disabled = true;
    try {
      const result = await json('/library/refresh', {});
      await loadLibrary();
      if (result.skipped) report(`有 ${result.skipped} 条本机记录无法读取。`, true);
    } catch (error) { report(error.message, true); }
    finally { button.disabled = false; }
  };

  document.addEventListener('prisma-project-change', () => {
    const scope = get('library-scope');
    scope.querySelector('option[value="current"]').disabled = !openProject;
    scope.value = openProject ? 'current' : 'shared';
    selectedPrompt = null;
    backgroundPrompt = null;
    backgroundSelection = null;
    otherPrompt = null;
    appliedDefaults = '';
    get('prompt').value = '';
    get('negative').value = get('negative').defaultValue;
    currentJob = '';
    activeJob = null;
    catalog = null;
    sourceItems = [];
    addedImages.clear();
    uploadedCaptions.clear();
    get('dataset-items').replaceChildren();
    setDatasetDownload('');
    setHistory([]);
    renderJob(null);
    controls();
    if (!field('assets').hidden) loadLibrary();
    refresh().catch((error) => report(error.message, true));
  });
  document.addEventListener('prisma-provider-catalog', () => {
    controls();
  });

  controls();
  if (!field('assets').hidden) loadLibrary();
  refresh().then(() => report('图像工作室已就绪。')).catch((error) => report(error.message, true));
})();
