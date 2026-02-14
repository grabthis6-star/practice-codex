document.addEventListener("DOMContentLoaded", () => {
  const img = document.getElementById("roi-image");
  const box = document.getElementById("roi-box");

  const xInput = document.getElementById("x");
  const yInput = document.getElementById("y");
  const wInput = document.getElementById("w");
  const hInput = document.getElementById("h");

  const xNumber = document.getElementById("roi-x");
  const yNumber = document.getElementById("roi-y");
  const wNumber = document.getElementById("roi-w");
  const hNumber = document.getElementById("roi-h");
  const resetBtn = document.getElementById("roi-reset");

  if (!img || !box || !xInput || !yInput || !wInput || !hInput) return;

  const MIN_SIZE = 5;
  const state = {
    mode: null,
    handle: null,
    startPoint: null,
    startRect: null,
    rect: null,
  };

  function getScale() {
    const imgRect = img.getBoundingClientRect();
    return {
      scaleX: img.naturalWidth / imgRect.width,
      scaleY: img.naturalHeight / imgRect.height,
      boundsW: imgRect.width,
      boundsH: imgRect.height,
    };
  }

  function clampRect(rect) {
    const { boundsW, boundsH } = getScale();
    const x = Math.max(0, Math.min(rect.x, boundsW));
    const y = Math.max(0, Math.min(rect.y, boundsH));
    const maxW = boundsW - x;
    const maxH = boundsH - y;
    const w = Math.max(0, Math.min(rect.w, maxW));
    const h = Math.max(0, Math.min(rect.h, maxH));
    return { x, y, w, h };
  }

  function pointFromEvent(e) {
    const rect = img.getBoundingClientRect();
    const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
    const y = Math.max(0, Math.min(e.clientY - rect.top, rect.height));
    return { x, y };
  }

  function displayRectToReal(rect) {
    const { scaleX, scaleY } = getScale();
    return {
      x: Math.round(rect.x * scaleX),
      y: Math.round(rect.y * scaleY),
      w: Math.round(rect.w * scaleX),
      h: Math.round(rect.h * scaleY),
    };
  }

  function realRectToDisplay(rect) {
    const { scaleX, scaleY, boundsW, boundsH } = getScale();
    return clampRect({
      x: rect.x / scaleX,
      y: rect.y / scaleY,
      w: rect.w / scaleX,
      h: rect.h / scaleY,
    }, boundsW, boundsH);
  }

  function applyRect(rect, syncNumber = true) {
    const normalized = clampRect(rect);
    state.rect = normalized;

    if (normalized.w < MIN_SIZE || normalized.h < MIN_SIZE) {
      box.style.display = "none";
    } else {
      box.style.display = "block";
      box.style.left = `${normalized.x}px`;
      box.style.top = `${normalized.y}px`;
      box.style.width = `${normalized.w}px`;
      box.style.height = `${normalized.h}px`;
    }

    const real = displayRectToReal(normalized);
    xInput.value = real.x;
    yInput.value = real.y;
    wInput.value = real.w;
    hInput.value = real.h;

    if (syncNumber && xNumber && yNumber && wNumber && hNumber) {
      xNumber.value = real.x;
      yNumber.value = real.y;
      wNumber.value = real.w;
      hNumber.value = real.h;
    }
  }

  function clearRect() {
    state.rect = null;
    box.style.display = "none";
    xInput.value = "";
    yInput.value = "";
    wInput.value = "";
    hInput.value = "";

    if (xNumber && yNumber && wNumber && hNumber) {
      xNumber.value = 0;
      yNumber.value = 0;
      wNumber.value = 0;
      hNumber.value = 0;
    }
  }

  function resizeByHandle(base, handle, dx, dy) {
    let left = base.x;
    let top = base.y;
    let right = base.x + base.w;
    let bottom = base.y + base.h;

    if (handle.includes("n")) top += dy;
    if (handle.includes("s")) bottom += dy;
    if (handle.includes("w")) left += dx;
    if (handle.includes("e")) right += dx;

    if (right < left) [left, right] = [right, left];
    if (bottom < top) [top, bottom] = [bottom, top];

    return clampRect({ x: left, y: top, w: right - left, h: bottom - top });
  }

  function startDraw(e) {
    state.mode = "draw";
    const p = pointFromEvent(e);
    state.startPoint = p;
    state.startRect = { x: p.x, y: p.y, w: 0, h: 0 };
    applyRect(state.startRect);
  }

  function startMove(e) {
    if (!state.rect) return;
    state.mode = "move";
    state.startPoint = pointFromEvent(e);
    state.startRect = { ...state.rect };
  }

  function startResize(e, handle) {
    if (!state.rect) return;
    state.mode = "resize";
    state.handle = handle;
    state.startPoint = pointFromEvent(e);
    state.startRect = { ...state.rect };
  }

  img.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    startDraw(e);
  });

  box.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || !state.rect) return;
    e.preventDefault();

    const handle = e.target.dataset.handle;
    if (handle) {
      startResize(e, handle);
    } else {
      startMove(e);
    }
  });

  window.addEventListener("mousemove", (e) => {
    if (!state.mode || !state.startPoint || !state.startRect) return;

    const p = pointFromEvent(e);
    const dx = p.x - state.startPoint.x;
    const dy = p.y - state.startPoint.y;

    if (state.mode === "draw") {
      const start = state.startPoint;
      applyRect({
        x: Math.min(start.x, p.x),
        y: Math.min(start.y, p.y),
        w: Math.abs(p.x - start.x),
        h: Math.abs(p.y - start.y),
      });
      return;
    }

    if (state.mode === "move") {
      const moved = clampRect({
        x: state.startRect.x + dx,
        y: state.startRect.y + dy,
        w: state.startRect.w,
        h: state.startRect.h,
      });

      moved.x = Math.min(moved.x, getScale().boundsW - moved.w);
      moved.y = Math.min(moved.y, getScale().boundsH - moved.h);
      applyRect(moved);
      return;
    }

    if (state.mode === "resize" && state.handle) {
      applyRect(resizeByHandle(state.startRect, state.handle, dx, dy));
    }
  });

  window.addEventListener("mouseup", () => {
    if (!state.mode) return;
    if (state.rect && (state.rect.w < MIN_SIZE || state.rect.h < MIN_SIZE)) {
      clearRect();
    }
    state.mode = null;
    state.handle = null;
    state.startPoint = null;
    state.startRect = null;
  });

  if (xNumber && yNumber && wNumber && hNumber) {
    [xNumber, yNumber, wNumber, hNumber].forEach((input) => {
      input.addEventListener("input", () => {
        const nextReal = {
          x: Math.max(0, Number(xNumber.value) || 0),
          y: Math.max(0, Number(yNumber.value) || 0),
          w: Math.max(0, Number(wNumber.value) || 0),
          h: Math.max(0, Number(hNumber.value) || 0),
        };
        applyRect(realRectToDisplay(nextReal), false);
      });
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      clearRect();
    });
  }

  function initFromHidden() {
    const base = {
      x: Number(xInput.value),
      y: Number(yInput.value),
      w: Number(wInput.value),
      h: Number(hInput.value),
    };

    if (!base.w || !base.h) {
      clearRect();
      return;
    }

    applyRect(realRectToDisplay(base));
  }

  if (img.complete) {
    initFromHidden();
  } else {
    img.addEventListener("load", initFromHidden, { once: true });
  }

  window.addEventListener("resize", () => {
    if (!xInput.value || !yInput.value || !wInput.value || !hInput.value) return;
    initFromHidden();
  });
});
