(() => {
  const img = document.getElementById('roi-image');
  const box = document.getElementById('roi-box');
  const form = document.getElementById('roi-form');
  if (!img || !box || !form) return;

  const xInput = document.getElementById('x');
  const yInput = document.getElementById('y');
  const wInput = document.getElementById('w');
  const hInput = document.getElementById('h');

  let dragging = false;
  let startX = 0;
  let startY = 0;

  const relPos = (e) => {
    const rect = img.getBoundingClientRect();
    const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
    const y = Math.max(0, Math.min(e.clientY - rect.top, rect.height));
    return { x, y, rect };
  };

  const draw = (x1, y1, x2, y2) => {
    const left = Math.min(x1, x2);
    const top = Math.min(y1, y2);
    const width = Math.abs(x1 - x2);
    const height = Math.abs(y1 - y2);

    box.style.display = 'block';
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    box.style.width = `${width}px`;
    box.style.height = `${height}px`;
  };

  img.addEventListener('mousedown', (e) => {
    dragging = true;
    const p = relPos(e);
    startX = p.x;
    startY = p.y;
    draw(startX, startY, startX, startY);
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const p = relPos(e);
    draw(startX, startY, p.x, p.y);
  });

  window.addEventListener('mouseup', (e) => {
    if (!dragging) return;
    dragging = false;
    const p = relPos(e);

    const x = Math.min(startX, p.x);
    const y = Math.min(startY, p.y);
    const w = Math.abs(startX - p.x);
    const h = Math.abs(startY - p.y);

    const scaleX = img.naturalWidth / img.clientWidth;
    const scaleY = img.naturalHeight / img.clientHeight;

    xInput.value = Math.round(x * scaleX);
    yInput.value = Math.round(y * scaleY);
    wInput.value = Math.round(w * scaleX);
    hInput.value = Math.round(h * scaleY);
  });

  form.addEventListener('submit', (e) => {
    if (!wInput.value || !hInput.value || Number(wInput.value) <= 0 || Number(hInput.value) <= 0) {
      e.preventDefault();
      alert('ROI를 먼저 드래그로 지정하세요.');
    }
  });
})();
