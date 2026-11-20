function layoutRoadmap({nodes, edges}) {
  const nodeWidth = 152;
  const nodeHeight = 58;
  const rowGap = 34;
  const separation = nodeHeight + rowGap;
  const margin = 18;
  const byId = new Map(nodes.map(({id}, order) => [id, {
    id, order, rank: 0, parents: [], children: [], predecessor: null, row: 0,
  }]));
  for (const {source, target, feedback} of edges) {
    if (feedback) continue;
    const from = byId.get(source);
    const to = byId.get(target);
    from.children.push(to);
    to.parents.push(from);
  }
  const degrees = new Map([...byId.values()].map((node) => [node, node.parents.length]));
  const sorted = [...byId.values()].filter((node) => !degrees.get(node));
  for (const node of sorted) {
    for (const child of node.children) {
      if (node.rank + 1 > child.rank) {
        child.rank = node.rank + 1;
        child.predecessor = node;
      }
      degrees.set(child, degrees.get(child) - 1);
      if (!degrees.get(child)) sorted.push(child);
    }
  }
  const last = sorted.reduce((furthest, node) => node.rank > furthest.rank ? node : furthest, sorted[0]);
  const spine = new Set();
  for (let node = last; node; node = node.predecessor) spine.add(node);
  for (const node of [...sorted].reverse()) {
    if (!spine.has(node) && node.children.length) {
      node.rank = Math.min(...node.children.map((child) => child.rank)) - 1;
    }
  }
  const columns = Array.from({length: last.rank + 1}, () => []);
  for (const node of sorted) columns[node.rank].push(node);
  for (const column of columns) column.sort((a, b) => a.order - b.order);

  // Alternating barycentric sweeps reduce crossings and keep connected nodes close.
  const nodeRows = () => new Map(columns.flatMap((column) => column.map((node, row) => [node, row])));
  const reorder = (index, key, positions) => {
    const column = columns[index];
    const previous = new Map(column.map((node, row) => [node, row]));
    const barycenter = (node) => {
      const neighbors = node[key];
      return neighbors.length
        ? neighbors.reduce((total, neighbor) => total + positions.get(neighbor), 0) / neighbors.length
        : previous.get(node);
    };
    column.sort((a, b) => barycenter(a) - barycenter(b) || previous.get(a) - previous.get(b));
  };
  for (let pass = 0; pass < 12; pass++) {
    let positions = nodeRows();
    for (let index = 1; index < columns.length; index++) {
      reorder(index, 'parents', positions);
      positions = nodeRows();
    }
    for (let index = columns.length - 2; index >= 0; index--) {
      reorder(index, 'children', positions);
      positions = nodeRows();
    }
  }

  const rowCounts = columns.map((column) => column.length);
  const rows = Math.max(...rowCounts);
  const upperTracks = edges.filter((edge) => !edge.feedback
    && byId.get(edge.source).rank + 1 !== byId.get(edge.target).rank).length;
  const top = margin + upperTracks * 10 + (upperTracks ? 12 : 0);
  const canvasCenter = top + nodeHeight / 2 + (rows - 1) * separation / 2;
  const centers = new Map();
  for (const column of columns) {
    const offset = (rows - column.length) * separation / 2;
    column.forEach((node, row) => centers.set(node, top + offset + nodeHeight / 2 + row * separation));
  }

  function compact(targets) {
    const pools = [];
    for (const [index, value] of targets.entries()) {
      pools.push({start: index, end: index, sum: value - index * separation, count: 1});
      while (pools.length > 1) {
        const right = pools.at(-1);
        const left = pools.at(-2);
        if (left.sum / left.count <= right.sum / right.count) break;
        pools.splice(-2, 2, {
          start: left.start, end: right.end,
          sum: left.sum + right.sum, count: left.count + right.count,
        });
      }
    }
    const result = [];
    for (const pool of pools) {
      const level = pool.sum / pool.count;
      for (let index = pool.start; index <= pool.end; index++) result[index] = level + index * separation;
    }
    const shift = canvasCenter - result.reduce((total, value) => total + value, 0) / result.length;
    return result.map((value) => value + shift);
  }
  const gaps = Array(columns.length + 1).fill(0);
  let upperLanes = 0;
  let lowerLanes = 0;
  const routes = edges.map((edge) => {
    const from = byId.get(edge.source);
    const to = byId.get(edge.target);
    const exitGap = from.rank + 1;
    const entryGap = to.rank;
    const out = {gap: exitGap, track: gaps[exitGap]++};
    const direct = !edge.feedback && exitGap === entryGap;
    const entry = direct ? out : {gap: entryGap, track: gaps[entryGap]++};
    const lane = direct ? null : edge.feedback ? lowerLanes++ : upperLanes++;
    return {edge, from, to, out, entry, lane};
  });
  const gapLeft = [];
  const columnLeft = [];
  let width = margin;
  for (let column = 0; column < gaps.length; column++) {
    gapLeft[column] = width;
    const occupied = gaps[column] * 9 + 24;
    width += column === 0 || column === columns.length
      ? gaps[column] ? occupied : 0 : Math.max(occupied, 52);
    if (column < columns.length) {
      columnLeft[column] = width;
      width += nodeWidth;
    }
  }
  width += margin;
  const neighbors = new Map([...byId.values()].map((node) => [node, []]));
  for (const {source, target} of edges) {
    const from = byId.get(source);
    const to = byId.get(target);
    const dx = Math.max(1, Math.abs(columnLeft[from.rank] - columnLeft[to.rank]));
    neighbors.get(from).push({node: to, dx});
    neighbors.get(to).push({node: from, dx});
  }
  // Reweighted least squares approximates the mean Euclidean arrow length;
  // PAVA enforces a clear vertical gap while keeping each column in order.
  for (let pass = 0; pass < 48; pass++) {
    for (const order of [columns, [...columns].reverse()]) {
      for (const column of order) {
        const targets = column.map((node) => {
          const links = neighbors.get(node);
          if (!links.length) return centers.get(node);
          let total = 0;
          let weights = 0;
          for (const {node: neighbor, dx} of links) {
            const dy = centers.get(node) - centers.get(neighbor);
            const weight = 1 / Math.max(1, Math.hypot(dx, dy));
            total += centers.get(neighbor) * weight;
            weights += weight;
          }
          return total / weights;
        });
        compact(targets).forEach((center, index) => centers.set(column[index], center));
      }
    }
  }
  for (const column of columns) column.forEach((node, row) => { node.row = row; });

  const lowerTracks = routes.filter((route) => route.edge.feedback).length;
  const lowestCenter = Math.min(...centers.values());
  const shiftDown = Math.max(0, top + nodeHeight / 2 - lowestCenter);
  if (shiftDown) {
    for (const [node, center] of centers) centers.set(node, center + shiftDown);
  }
  const lowestBottom = Math.max(...centers.values()) + nodeHeight / 2 + margin;
  const height = lowestBottom + (lowerTracks ? 12 + lowerTracks * 10 : 0);
  const boxes = new Map([...byId.values()].map((node) => [node.id, {
    x: columnLeft[node.rank], y: centers.get(node) - nodeHeight / 2,
    width: nodeWidth, height: nodeHeight,
  }]));

  function portOffsets(nodeKey, neighborKey) {
    const groups = new Map();
    for (const route of routes) {
      const node = route[nodeKey];
      if (!groups.has(node)) groups.set(node, []);
      groups.get(node).push(route);
    }
    const offsets = new Map();
    for (const group of groups.values()) {
      group.sort((a, b) => centers.get(a[neighborKey]) - centers.get(b[neighborKey])
        || a[neighborKey].order - b[neighborKey].order);
      const spread = Math.min(32, (group.length - 1) * 8);
      group.forEach((route, index) => {
        const offset = group.length === 1 ? 0 : spread * (index / (group.length - 1) - .5);
        offsets.set(route, offset);
      });
    }
    return offsets;
  }
  const fromOffset = portOffsets('from', 'to');
  const toOffset = portOffsets('to', 'from');
  const trackX = ({gap, track}) => gapLeft[gap] + 12 + track * 9;
  const paths = routes.map((route) => {
    const {edge, from, to, out, entry, lane} = route;
    const source = boxes.get(from.id);
    const target = boxes.get(to.id);
    const startY = centers.get(from) + fromOffset.get(route);
    const endY = centers.get(to) + toOffset.get(route);
    const start = `M${source.x + nodeWidth} ${startY} H${trackX(out)}`;
    const bypassY = edge.feedback ? height - margin - 12 - lane * 10 : margin + lane * 10;
    const bypass = lane === null ? '' : ` V${bypassY} H${trackX(entry)}`;
    return {...edge, path: `${start}${bypass} V${endY} H${target.x - 2}`};
  });
  return {width, height, boxes, paths};
}
