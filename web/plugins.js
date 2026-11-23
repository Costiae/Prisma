(() => {
  const list = field('plugin-list');
  const status = field('plugin-list-status');
  let sequence = 0;

  function display(result) {
    list.replaceChildren(...result.plugins.map((plugin) => {
      const row = document.createElement('li');
      const body = document.createElement('div');
      const title = document.createElement('h3');
      title.textContent = plugin.title;
      const description = document.createElement('p');
      description.textContent = plugin.description;
      const nodes = document.createElement('p');
      nodes.className = 'hint';
      nodes.textContent = plugin.nodes.map((node) => node.title).join('、');
      body.append(title, description, nodes);

      const actions = document.createElement('div');
      actions.className = 'plugin-actions';
      const badge = document.createElement('span');
      badge.className = 'stage-status';
      badge.dataset.status = plugin.enabled ? 'released' : 'skipped';
      badge.textContent = plugin.enabled ? '已启用' : '已停用';
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = plugin.enabled ? '停用' : '启用';
      button.setAttribute('aria-label', `${button.textContent}${plugin.title}`);
      button.addEventListener('click', () => loadPluginState({id: plugin.id, enabled: !plugin.enabled}));
      actions.append(badge, button);
      row.append(body, actions);
      return row;
    }));
    syncNodeNavigation(result.nodes);
    document.dispatchEvent(new CustomEvent('prisma-plugins-change', {detail: result}));
  }

  async function loadPluginState(change) {
    const current = ++sequence;
    if (change) for (const button of list.querySelectorAll('button')) button.disabled = true;
    status.textContent = change ? '正在更新插件…' : '正在读取插件…';
    try {
      const response = await request(change ? '/api/plugins/set-enabled' : '/api/plugins/list', change || {});
      const result = await response.json();
      if (current !== sequence) return;
      display(result);
      status.textContent = change ? (change.enabled ? '插件已启用。' : '插件已停用。')
        : result.plugins.length ? '' : '暂无已登记的插件。';
      if (change) {
        document.dispatchEvent(new Event('prisma-pipeline-refresh'));
        list.querySelectorAll('button')[result.plugins.findIndex((plugin) => plugin.id === change.id)]?.focus();
      }
    } catch (error) {
      if (current === sequence) {
        status.textContent = error.message;
        for (const button of list.querySelectorAll('button')) button.disabled = false;
      }
    }
  }

  document.addEventListener('prisma-view-change', ({detail}) => {
    if (detail === 'plugin-manager') loadPluginState();
  });
  loadPluginState();
})();
