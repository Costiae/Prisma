function attachStoryPreview(readScene) {
  const get = (name) => field(`story-preview${name ? `-${name}` : ''}`);
  const dialog = get('');
  const output = get('output');
  const tracks = ['music', 'sound', 'voice'].map(get);
  const layers = ['bg', 'actor'].map(get);
  let runId = 0;

  function reset() {
    ++runId;
    for (const layer of layers) layer.replaceChildren();
    for (const track of tracks) {
      track.pause();
      track.removeAttribute('src');
    }
  }
  dialog.addEventListener('close', reset);

  function element(tag, text) {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  }

  function button(text, callback) {
    const node = element('button', text);
    node.type = 'button';
    node.onclick = callback;
    return node;
  }

  function play(text, assets, scene) {
    reset();
    const token = runId;
    let segment;
    let index = 0;
    const returns = [];
    dialog.showModal();

    function finish(message) {
      output.replaceChildren(element('p', message));
    }

    async function next(target) {
      for (const control of output.querySelectorAll('button')) control.disabled = true;
      for (let steps = 0; steps < 200 && token === runId; steps++) {
        if (target) {
          try {
            segment = await readScene(text, target);
          } catch (error) {
            if (token === runId) finish(error.message);
            return;
          }
          if (token !== runId) return;
          if (!segment.scene) return finish(`找不到场景 ${target}。`);
          index = 0;
          target = '';
        }
        const row = segment.rows[index++];
        if (!row) {
          const sceneIndex = segment.scenes.findIndex((item) => item.name === segment.scene);
          target = segment.scenes[sceneIndex + 1]?.name;
          if (!target) return finish('剧情结束。');
        } else if (row.kind === 'return') {
          const caller = returns.pop();
          if (!caller) return finish('剧情结束。');
          ({segment, index} = caller);
        }
        else if (row.kind === 'jump') target = row.target;
        else if (row.kind === 'call') {
          returns.push({segment, index});
          target = row.target;
        }
        else if (row.kind === 'menu') {
          output.replaceChildren(...row.options.map((option) => button(option.content, () => next(option.target))));
          return;
        } else if (row.kind === 'dialogue') {
          const name = segment.characters[row.speaker] || row.speaker || '旁白';
          output.replaceChildren(element('strong', name), element('p', row.content), button('继续', () => next()));
          return;
        } else if (row.kind === 'stop') tracks[0].pause();
        else if (row.kind === 'pause') {
          output.replaceChildren(element('p', `等待 ${row.content} 秒`), button('继续', () => next()));
          return;
        }
        else if (row.kind === 'source' || row.kind === 'screen') return finish(`场景 ${segment.scene} 含原生 Ren’Py 命令。请用 Ren’Py SDK 运行此工程。`);
        else if (row.kind === 'media') {
          const ident = assets[row.path];
          if (!ident) return finish(`场景 ${segment.scene} 中有素材尚未登记。`);
          const url = `/api/workspace/assets/${encodeURIComponent(ident)}?preview=1`;
          if (row.action === 'background' || row.action === 'portrait') {
            const image = document.createElement('img');
            image.alt = '';
            image.src = url;
            image.addEventListener('error', () => image.remove(), {once: true});
            layers[row.action === 'background' ? 0 : 1].replaceChildren(image);
          } else {
            const player = get(row.action);
            player.src = url;
            player.play().catch(() => {});
          }
        }
      }
      if (token === runId) finish('跳转次数过多，请检查场景之间是否形成循环。');
    }

    finish('正在读取场景…');
    next(scene);
  }

  return {play, close() { if (dialog.open) dialog.close(); }};
}
