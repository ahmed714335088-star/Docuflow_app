(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const stageOrder = ["script", "voice", "visuals", "subtitles", "render"];
  let aspectRatio = "16:9";
  let toastTimer;
  const keyIds = ["groq-key", "gemini-key", "pixabay-key"];

  function toast(message, error = false) {
    const box = $("#toast");
    box.textContent = message;
    box.classList.toggle("error", error);
    box.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove("show"), 3600);
  }

  function keysFromInputs() {
    return { groq: $("#groq-key").value.trim(), gemini: $("#gemini-key").value.trim(), pixabay: $("#pixabay-key").value.trim() };
  }

  function loadSettings() {
    try {
      const values = JSON.parse(localStorage.getItem("docuflow.keys.v1") || "{}");
      if (Object.hasOwn(values, "pexels")) {
        delete values.pexels;
        localStorage.setItem("docuflow.keys.v1", JSON.stringify(values));
      }
      $("#groq-key").value = values.groq || "";
      $("#gemini-key").value = values.gemini || "";
      $("#pixabay-key").value = values.pixabay || "";
      if (values.provider) {
        const radio = $(`input[name="provider"][value="${values.provider}"]`);
        if (radio) radio.checked = true;
      }
      if (values.voice) $("#voice").value = values.voice;
      updateProviderHint();
    } catch (_) { /* Ignore invalid browser-local preferences. */ }
  }

  function saveSettings(showToast = true) {
    const values = { ...keysFromInputs(), provider: selectedProvider(), voice: $("#voice").value };
    localStorage.setItem("docuflow.keys.v1", JSON.stringify(values));
    $("#settings-feedback").textContent = "حُفظت الإعدادات في هذا المتصفح.";
    if (showToast) toast("حُفظت إعدادات هذا المتصفح.");
    setTimeout(() => { $("#settings-feedback").textContent = ""; }, 3200);
  }

  function selectedProvider() { return $('input[name="provider"]:checked').value; }

  function updateProviderHint() {
    const provider = selectedProvider();
    const note = $("#provider-hint");
    if (provider === "demo") note.lastElementChild.textContent = "الوضع التجريبي لا يحتاج مفتاحاً؛ يستخدم سيناريو جاهزاً لإنشاء الصوت والترجمة ومعاينة الواجهة.";
    if (provider === "groq") note.lastElementChild.textContent = keysFromInputs().groq ? "سيُنشأ النص والترجمة عبر Groq. أضف مفتاح Pixabay اختيارياً لجلب لقطات حقيقية." : "أضف مفتاح Groq من صفحة الإعدادات قبل البدء. Pixabay اختياري ويمكن استخدام خلفيات محلية.";
    if (provider === "gemini") note.lastElementChild.textContent = keysFromInputs().gemini ? "سيُنشأ النص والترجمة عبر Gemini. أضف مفتاح Pixabay اختيارياً لجلب لقطات حقيقية." : "أضف مفتاح Gemini من صفحة الإعدادات قبل البدء. Pixabay اختياري ويمكن استخدام خلفيات محلية.";
  }

  function navigate(name) {
    $$(".view").forEach(view => view.classList.toggle("active", view.id === `view-${name}`));
    $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === name));
    const titles = { studio: "استوديو الإنتاج", history: "أرشيف الفيديو", settings: "الإعدادات والمفاتيح" };
    $("#crumb-current").textContent = titles[name];
    if (name === "history") refreshHistory();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function resetStages() {
    $$(".stage-row").forEach(row => {
      row.classList.remove("active", "complete", "error");
      $(".stage-status", row).textContent = "بانتظار";
    });
    $("#live-log").textContent = "";
    $("#progress-fill").style.width = "0%";
    $("#progress-percent").textContent = "0%";
  }

  function updateProgress(event) {
    const progress = Math.max(0, Math.min(100, Number(event.progress || 0)));
    $("#progress-fill").style.width = `${progress}%`;
    $("#progress-percent").textContent = `${progress}%`;
    $("#progress-message").textContent = event.message || "جارٍ تنفيذ المهمة…";
    const currentIndex = stageOrder.indexOf(event.stage);
    $$(".stage-row").forEach((row, index) => {
      row.classList.remove("active", "complete", "error");
      const status = $(".stage-status", row);
      if (event.status === "failed" && index === currentIndex) {
        row.classList.add("error"); status.textContent = "تعذّر";
      } else if (event.status === "completed" || (currentIndex >= 0 && index < currentIndex) || event.stage === "topic_done") {
        row.classList.add("complete"); status.textContent = "مكتمل";
      } else if (index === currentIndex && event.status !== "completed") {
        row.classList.add("active"); status.textContent = "جارٍ";
      } else if (index > currentIndex) {
        status.textContent = "التالي";
      }
    });
    if (event.message) {
      const line = document.createElement("div");
      line.textContent = `› ${event.message}`;
      $("#live-log").append(line);
      while ($("#live-log").children.length > 3) $("#live-log").firstElementChild.remove();
    }
  }

  function displayResult(result) {
    if (!result) return;
    const video = $("#result-video");
    video.src = `/api/videos/${encodeURIComponent(result.filename)}`;
    video.load();
    $("#download-button").href = `/api/download/${encodeURIComponent(result.filename)}`;
    $("#download-button").download = result.filename;
    $("#result-title").textContent = result.title || result.topic || "فيديو وثائقي";
    const mins = Math.floor(Number(result.duration_seconds || 0) / 60);
    const secs = Math.round(Number(result.duration_seconds || 0) % 60);
    $("#result-meta").textContent = `${result.aspect_ratio || "16:9"} · ${mins}:${String(secs).padStart(2, "0")} · ${result.voice || "edge-tts"}`;
    $("#result-source").textContent = result.demo ? "وضع تجريبي · لقطات محلية" : (result.visual_source || "تم الإنتاج");
    $("#result-section").hidden = false;
    $("#result-section").scrollIntoView({ behavior: "smooth", block: "start" });
    refreshHistory();
  }

  async function getJob(jobId) {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (!response.ok) throw new Error("تعذر قراءة حالة المهمة.");
    return response.json();
  }

  async function pollJob(jobId) {
    try {
      const job = await getJob(jobId);
      updateProgress(job);
      if (job.status === "completed") {
        displayResult((job.results || []).at(-1));
        $("#generate-button").disabled = false;
        $("#generate-button").querySelector("span:nth-child(2)").textContent = "ابدأ إنتاج الفيديو";
        toast(`اكتمل إنتاج ${job.results.length} فيديو. تم تحديث الأرشيف.`);
        return;
      }
      if (job.status === "failed") {
        $("#generate-button").disabled = false;
        $("#generate-button").querySelector("span:nth-child(2)").textContent = "أعد المحاولة";
        toast(job.error || job.message || "تعذّر إكمال الفيديو.", true);
        return;
      }
      setTimeout(() => pollJob(jobId), 900);
    } catch (error) {
      $("#generate-button").disabled = false;
      toast(error.message, true);
    }
  }

  async function generate() {
    const topics = $("#topics").value.trim();
    if (!topics) { toast("أضف موضوعاً واحداً على الأقل.", true); $("#topics").focus(); return; }
    if (topics.split("\n").filter(line => line.trim()).length > 8) { toast("الحد الأقصى 8 موضوعات في الدفعة.", true); return; }
    const provider = selectedProvider();
    if (provider === "groq" && !$("#groq-key").value.trim()) { navigate("settings"); toast("أضف مفتاح Groq أولاً.", true); return; }
    if (provider === "gemini" && !$("#gemini-key").value.trim()) { navigate("settings"); toast("أضف مفتاح Gemini أولاً.", true); return; }
    const button = $("#generate-button");
    button.disabled = true;
    button.querySelector("span:nth-child(2)").textContent = "جارٍ تجهيز خط الإنتاج…";
    $("#result-section").hidden = true;
    resetStages();
    try {
      saveSettings(false);
      const response = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topics,
          aspect_ratio: aspectRatio,
          voice: $("#voice").value,
          provider,
          keys: keysFromInputs(),
          whisper_model: "tiny",
          background_music: true,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "تعذر بدء الإنتاج.");
      $("#progress-message").textContent = "بدأ خط الإنتاج — جارٍ إعداد السيناريو.";
      pollJob(data.job_id);
    } catch (error) {
      button.disabled = false;
      button.querySelector("span:nth-child(2)").textContent = "أعد المحاولة";
      toast(error.message || "حدث خطأ في الاتصال.", true);
    }
  }

  async function refreshHistory() {
    try {
      const response = await fetch("/api/history", { cache: "no-store" });
      const data = await response.json();
      const items = data.items || [];
      $("#history-count").textContent = String(items.length).padStart(2, "0");
      $("#archive-total").textContent = items.length;
      $("#history-empty").hidden = items.length > 0;
      const grid = $("#history-grid");
      grid.replaceChildren();
      items.forEach(item => {
        const card = document.createElement("article");
        card.className = "history-card";
        const thumb = document.createElement("div"); thumb.className = "history-thumb";
        const video = document.createElement("video"); video.src = `/api/videos/${encodeURIComponent(item.filename)}#t=1`; video.muted = true; video.preload = "metadata"; video.playsInline = true; thumb.append(video);
        const body = document.createElement("div"); body.className = "history-body";
        const title = document.createElement("h3"); title.textContent = item.title || item.topic || "فيديو وثائقي";
        const meta = document.createElement("p");
        const date = item.created_at ? new Date(item.created_at).toLocaleDateString("ar", { year: "numeric", month: "short", day: "numeric" }) : "";
        const mb = (Number(item.size_bytes || 0) / 1048576).toFixed(1);
        meta.textContent = `${date} · ${item.aspect_ratio || "16:9"} · ${mb} MB`;
        const actions = document.createElement("div"); actions.className = "history-actions";
        const play = document.createElement("button"); play.type = "button"; play.textContent = "معاينة"; play.addEventListener("click", () => { navigate("studio"); displayResult(item); });
        const download = document.createElement("a"); download.href = `/api/download/${encodeURIComponent(item.filename)}`; download.download = item.filename; download.textContent = "تنزيل MP4";
        actions.append(play, download); body.append(title, meta, actions); card.append(thumb, body); grid.append(card);
      });
    } catch (_) { /* Keep the dashboard usable if history is temporarily unavailable. */ }
  }

  $$(".nav-item").forEach(item => item.addEventListener("click", () => navigate(item.dataset.view)));
  $("#settings-shortcut").addEventListener("click", () => navigate("settings"));
  $("#generate-button").addEventListener("click", generate);
  $("#show-history").addEventListener("click", () => navigate("history"));
  $$("[data-open-studio]").forEach(button => button.addEventListener("click", () => navigate("studio")));
  $$(".ratio-option").forEach(button => button.addEventListener("click", () => {
    $$(".ratio-option").forEach(option => option.classList.remove("selected"));
    button.classList.add("selected"); aspectRatio = button.dataset.ratio;
  }));
  $$('input[name="provider"]').forEach(radio => radio.addEventListener("change", () => { updateProviderHint(); saveSettings(false); }));
  keyIds.forEach(id => $(`#${id}`).addEventListener("input", updateProviderHint));
  $("#voice").addEventListener("change", () => saveSettings(false));
  $("#save-settings").addEventListener("click", () => saveSettings(true));
  $("#clear-settings").addEventListener("click", () => {
    localStorage.removeItem("docuflow.keys.v1");
    keyIds.forEach(id => { $(`#${id}`).value = ""; });
    updateProviderHint();
    $("#settings-feedback").textContent = "مُسحت المفاتيح المحفوظة.";
    toast("مُسحت المفاتيح من هذا المتصفح.");
  });
  $$(".reveal-key").forEach(button => button.addEventListener("click", () => {
    const input = $(`#${button.dataset.target}`); const reveal = input.type === "password";
    input.type = reveal ? "text" : "password"; button.textContent = reveal ? "إخفاء" : "إظهار";
  }));
  $("#topics").addEventListener("input", event => {
    const count = event.target.value.split("\n").filter(line => line.trim()).length;
    $("#topic-count").textContent = `${count} / 8`;
    $("#topic-count").style.color = count > 8 ? "#dc897a" : "";
  });
  $$('button[data-view="studio"]').forEach(button => button.addEventListener("dblclick", () => { $("#topics").focus(); }));
  loadSettings();
  refreshHistory();
})();
