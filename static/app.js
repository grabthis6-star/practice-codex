document.addEventListener("DOMContentLoaded", () => {
  const img = document.getElementById("roi-image");
  const box = document.getElementById("roi-box");

  // ROI 입력(hidden)
  const xInput = document.getElementById("x");
  const yInput = document.getElementById("y");
  const wInput = document.getElementById("w");
  const hInput = document.getElementById("h");

  // ROI 화면이 아닌 페이지에서는 그냥 종료
  if (!img || !box || !xInput || !yInput || !wInput || !hInput) return;

  let dragging = false;
  let startX = 0;
  let startY = 0;

  // 화면 좌표 -> 이미지 내부 좌표(보이는 크기 기준)로 변환
  function getPoint(e) {
    const rect = img.getBoundingClientRect();
    const clientX = e.clientX;
    const clientY = e.clientY;

    // 이미지 영역 밖에서 시작하면 무시할 수 있게 clamp
    const x = Math.max(0, Math.min(clientX - rect.left, rect.width));
    const y = Math.max(0, Math.min(clientY - rect.top, rect.height));
    return { x, y, rect };
  }

  function setBox(x, y, w, h) {
    box.style.display = "block";
    box.style.left = `${x}px`;
    box.style.top = `${y}px`;
    box.style.width = `${w}px`;
    box.style.height = `${h}px`;
  }

  function setInputsFromDisplayRect(displayX, displayY, displayW, displayH) {
    // 이미지가 화면에서 축소/확대되어 보이므로,
    // 실제 원본 픽셀 좌표로 변환해서 서버에 보내는 게 정확함.
    const rect = img.getBoundingClientRect();
    const scaleX = img.naturalWidth / rect.width;
    const scaleY = img.naturalHeight / rect.height;

    const realX = Math.round(displayX * scaleX);
    const realY = Math.round(displayY * scaleY);
    const realW = Math.round(displayW * scaleX);
    const realH = Math.round(displayH * scaleY);

    xInput.value = realX;
    yInput.value = realY;
    wInput.value = realW;
    hInput.value = realH;
  }

  img.addEventListener("mousedown", (e) => {
    // 왼쪽 클릭만
    if (e.button !== 0) return;

    dragging = true;
    const p = getPoint(e);
    startX = p.x;
    startY = p.y;

    // 시작점에서 0 크기로 박스 표시
    setBox(startX, startY, 0, 0);
    setInputsFromDisplayRect(startX, startY, 0, 0);
  });

  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;

    const p = getPoint(e);
    const endX = p.x;
    const endY = p.y;

    const x = Math.min(startX, endX);
    const y = Math.min(startY, endY);
    const w = Math.abs(endX - startX);
    const h = Math.abs(endY - startY);

    setBox(x, y, w, h);
    setInputsFromDisplayRect(x, y, w, h);
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;

    // 너무 작은 ROI는 실수일 수 있으니 숨길 수도 있음(원하면)
    const w = parseInt(box.style.width || "0", 10);
    const h = parseInt(box.style.height || "0", 10);
    if (w < 5 || h < 5) {
      box.style.display = "none";
      xInput.value = "";
      yInput.value = "";
      wInput.value = "";
      hInput.value = "";
    }
  });
});
