(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    dropzone: $("dropzone"),
    file: $("file"),
    fileChip: $("fileChip"),
    fileName: $("fileName"),
    fileMeta: $("fileMeta"),
    fmt: $("fmt"),
    dpi: $("dpi"),
    pages: $("pages"),
    sizeEnabled: $("sizeEnabled"),
    targetKb: $("targetKb"),
    quality: $("quality"),
    btnConvert: $("btnConvert"),
    btnEstimate: $("btnEstimate"),
    progress: $("progress"),
    status: $("status"),
    results: $("results"),
    prevPage: $("prevPage"),
    nextPage: $("nextPage"),
    pageLabel: $("pageLabel"),
    previewImg: $("previewImg"),
    previewPlaceholder: $("previewPlaceholder"),
  };

  let currentFile = null;
  let pageCount = 0;
  let currentPage = 1;

  function setStatus(msg, kind = "") {
    els.status.textContent = msg;
    els.status.className = "status" + (kind ? ` ${kind}` : "");
  }

  function setBusy(busy) {
    els.progress.classList.toggle("active", busy);
    els.btnConvert.disabled = busy || !currentFile;
    els.btnEstimate.disabled = busy || !currentFile;
  }

  function syncSizeControls() {
    const on = els.sizeEnabled.checked;
    els.targetKb.disabled = !on;
    const isJpeg = ["jpg", "jpeg"].includes(els.fmt.value);
    els.quality.disabled = on || !isJpeg;
  }

  els.sizeEnabled.addEventListener("change", syncSizeControls);
  els.fmt.addEventListener("change", syncSizeControls);
  syncSizeControls();

  ["dragenter", "dragover"].forEach((ev) => {
    els.dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      els.dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    els.dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("dragover");
    });
  });
  els.dropzone.addEventListener("drop", (e) => {
    const f = e.dataTransfer?.files?.[0];
    if (f) acceptFile(f);
  });
  els.file.addEventListener("change", () => {
    const f = els.file.files?.[0];
    if (f) acceptFile(f);
  });

  async function acceptFile(file) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setStatus("请选择 PDF 文件", "error");
      return;
    }
    currentFile = file;
    els.fileChip.classList.add("visible");
    els.fileName.textContent = file.name;
    els.fileMeta.textContent = formatBytes(file.size);
    els.results.innerHTML = "";
    setBusy(true);
    setStatus("正在读取 PDF 信息…");

    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/info", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "读取失败");
      pageCount = data.page_count;
      currentPage = 1;
      updatePager();
      await loadPreview();
      setStatus(`已加载 · 共 ${pageCount} 页`, "ok");
      els.btnConvert.disabled = false;
      els.btnEstimate.disabled = false;
    } catch (err) {
      pageCount = 0;
      updatePager();
      setStatus(err.message || String(err), "error");
      els.btnConvert.disabled = true;
      els.btnEstimate.disabled = true;
    } finally {
      els.progress.classList.remove("active");
    }
  }

  function updatePager() {
    els.pageLabel.textContent = pageCount ? `${currentPage} / ${pageCount}` : "— / —";
    els.prevPage.disabled = !pageCount || currentPage <= 1;
    els.nextPage.disabled = !pageCount || currentPage >= pageCount;
  }

  els.prevPage.addEventListener("click", async () => {
    if (currentPage > 1) {
      currentPage -= 1;
      updatePager();
      await loadPreview();
    }
  });
  els.nextPage.addEventListener("click", async () => {
    if (currentPage < pageCount) {
      currentPage += 1;
      updatePager();
      await loadPreview();
    }
  });

  async function loadPreview() {
    if (!currentFile) return;
    const fd = new FormData();
    fd.append("file", currentFile);
    fd.append("page", String(currentPage));
    const res = await fetch("/api/preview", { method: "POST", body: fd });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "预览失败");
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    els.previewImg.onload = () => URL.revokeObjectURL(url);
    els.previewImg.src = url;
    els.previewImg.hidden = false;
    els.previewPlaceholder.hidden = true;
  }

  function buildFormData() {
    const fd = new FormData();
    fd.append("file", currentFile);
    fd.append("fmt", els.fmt.value);
    fd.append("dpi", String(Number(els.dpi.value) || 150));
    fd.append("pages", els.pages.value.trim());
    fd.append(
      "target_size_kb",
      els.sizeEnabled.checked ? String(Number(els.targetKb.value) || 0) : "0"
    );
    fd.append("jpeg_quality", String(Number(els.quality.value) || 85));
    return fd;
  }

  els.btnEstimate.addEventListener("click", async () => {
    if (!currentFile) return;
    setBusy(true);
    setStatus("正在估算输出体积…");
    els.results.innerHTML = "";
    try {
      const fd = buildFormData();
      const res = await fetch("/api/convert-meta", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "估算失败");
      renderResults(data);
      setStatus(
        `估算完成 · ${data.count} 页 · 合计 ${formatBytes(data.total_bytes)}`,
        "ok"
      );
    } catch (err) {
      setStatus(err.message || String(err), "error");
    } finally {
      setBusy(false);
    }
  });

  els.btnConvert.addEventListener("click", async () => {
    if (!currentFile) return;
    setBusy(true);
    setStatus("正在转换，请稍候…");
    try {
      const fd = buildFormData();
      fd.append("as_zip", "true");
      const res = await fetch("/api/convert", { method: "POST", body: fd });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const detail = data.detail;
        throw new Error(
          typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail.map((d) => d.msg || d).join("; ")
              : "转换失败"
        );
      }
      const blob = await res.blob();
      const name = parseDownloadName(res.headers.get("Content-Disposition"), "images.zip");
      downloadBlob(blob, name);
      setStatus(`已下载 ${name}`, "ok");
    } catch (err) {
      setStatus(err.message || String(err), "error");
    } finally {
      setBusy(false);
    }
  });

  function renderResults(data) {
    els.results.innerHTML = "";
    for (const item of data.items || []) {
      const row = document.createElement("div");
      row.className = "result-item";
      const thumb = item.data_url
        ? `<img src="${item.data_url}" alt="page ${item.page}" />`
        : `<img alt="" style="opacity:.3" />`;
      const q =
        item.quality_used != null ? ` · q${item.quality_used}` : "";
      row.innerHTML = `
        ${thumb}
        <div>
          <div class="name">${escapeHtml(item.filename)}</div>
          <div class="hint">${item.width}×${item.height}${q}</div>
        </div>
        <div class="size">${item.size_kb} KB</div>
      `;
      els.results.appendChild(row);
    }
  }

  function parseDownloadName(dispo, fallback) {
    if (!dispo) return fallback;
    const star = /filename\*=UTF-8''([^;]+)/i.exec(dispo);
    if (star) {
      try {
        return decodeURIComponent(star[1]);
      } catch (_) {
        /* ignore */
      }
    }
    const plain = /filename="?([^";]+)"?/i.exec(dispo);
    return plain?.[1] || fallback;
  }

  function downloadBlob(blob, filename) {
    const a = document.createElement("a");
    const url = URL.createObjectURL(blob);
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  function formatBytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }
})();
