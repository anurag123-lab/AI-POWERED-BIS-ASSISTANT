/* Public landing-page interactions. */

function createCanvasAnimation(canvas, draw) {
  if (!canvas) return;

  const context = canvas.getContext('2d');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let frame = 0;
  let animationFrame;

  function resize() {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = canvas.clientWidth * ratio;
    canvas.height = canvas.clientHeight * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  function render() {
    draw(context, canvas.clientWidth, canvas.clientHeight, frame);
    if (!reduceMotion) {
      frame += 0.45;
      animationFrame = requestAnimationFrame(render);
    }
  }

  resize();
  window.addEventListener('resize', resize, { passive: true });
  render();

  return () => cancelAnimationFrame(animationFrame);
}

function drawNetwork(context, width, height, time) {
  context.clearRect(0, 0, width, height);
  const nodes = [];
  const count = Math.max(18, Math.floor(width / 62));

  for (let index = 0; index < count; index += 1) {
    const depth = (index * 0.37 + time * 0.002) % 1;
    const x = width * 0.5 + Math.sin(index * 2.8 + time * 0.01) * width * (0.18 + depth * 0.26);
    const y = height * (0.12 + ((index * 0.23 + time * 0.001) % 0.78));
    const radius = 1.5 + depth * 4;
    nodes.push({ x, y, radius, depth });
  }

  nodes.forEach((node, index) => {
    nodes.slice(index + 1).forEach((other) => {
      const distance = Math.hypot(node.x - other.x, node.y - other.y);
      if (distance < 170) {
        context.strokeStyle = `rgba(255, 107, 53, ${0.12 * (1 - distance / 170)})`;
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(node.x, node.y);
        context.lineTo(other.x, other.y);
        context.stroke();
      }
    });
  });

  nodes.forEach((node) => {
    const glow = context.createRadialGradient(node.x, node.y, 0, node.x, node.y, node.radius * 7);
    glow.addColorStop(0, `rgba(255, 180, 80, ${0.55 * node.depth})`);
    glow.addColorStop(1, 'rgba(255, 107, 53, 0)');
    context.fillStyle = glow;
    context.beginPath();
    context.arc(node.x, node.y, node.radius * 7, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = '#ff935e';
    context.beginPath();
    context.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
    context.fill();
  });
}

function drawSignalField(context, width, height, time) {
  context.clearRect(0, 0, width, height);
  context.lineWidth = 1;

  for (let row = 0; row < 13; row += 1) {
    context.beginPath();
    for (let column = 0; column <= width; column += 18) {
      const y = row * (height / 12) + Math.sin(column * 0.012 + time * 0.02 + row) * 10;
      if (column === 0) context.moveTo(column, y);
      else context.lineTo(column, y);
    }
    context.strokeStyle = `rgba(255, 107, 53, ${0.05 + (row % 3) * 0.018})`;
    context.stroke();
  }
}

createCanvasAnimation(document.getElementById('ai-network-canvas'), drawNetwork);
createCanvasAnimation(document.getElementById('landing-signal-canvas'), drawSignalField);
