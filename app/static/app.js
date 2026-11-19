(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const stageOrder = ["script", "voice", "visuals", "subtitles", "render"];
  const keyIds = ["groq-key", "gemini-key", "pixabay-key", "pollinations-key"];
  let aspectRatio = "16:9";
  let toastTimer;

  function toast(message, error = false) {
    const box = $("#toast");
    box.textContent = message;
    box.classList.toggle("error", error);
    box.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove("show"), 3600);
  }

  function keysFromInputs() {
    return { groq: $("#groq-key").value.trim(), gemini: $("#gemini-key").value.trim(), pixabay: $("#pixabay-key").value.trim(), pollinations: $("#pollinations-key").value.trim() };
  }

  function updateVoiceOptions(preferredVoice = "") {
    const language = $("#video-language").value;
    const voices = language === "ar"
      ? [
          ["ar-EG-ShakirNeural", "Shakir · مصري، وثائقي"],
          ["ar-SA-HamedNeural", "Hamed · سعودي، رصين"],
        ]
      : [
          ["en-US-ChristopherNeural", "Christopher · عميق وهادئ"],
          ["en-US-GuyNeural", "Guy · وثائقي رصين"],
          ["en-US-AndrewNeural", "Andrew · دافئ وواضح"],
          ["en-US-BrianNeural", "Brian · منخفض ومتزن"],
          ["en-US-EricNeural", "Eric · سرد سينمائي"],
        ];
    const select = $("#voice");
    select.replaceChildren(...voices.map(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }));
    select.value = voices.some(([value]) => value === preferredVoice) ? preferredVoice : voices[0][0];
    $("#voice-language-tag").textContent = language === "ar" ? "عربي" : "إنجليزي";
    $("#voice-hint").textContent = language === "ar"
      ? "أصوات عربية طبيعية؛ اختر اللهجة المصرية أو السعودية."
      : "أصوات إنجليزية طبيعية؛ تتبدل القائمة تلقائياً مع لغة الفيديو.";
  }

  function updateDurationLabel() {
    const minutes = Number($("#duration-minutes").value || 1);
    const scenes = Math.ceil((minutes * 60 - 0.22) / (4 - 0.22));
    $("#scene-duration-chip").innerHTML = `${scenes} مشهداً <i></i> 4 ثوانٍ كحد أقصى`;
    $("#duration-note").textContent = `${scenes} مشهداً · ${minutes} ${minutes === 1 ? "دقيقة" : "دقائق"} · حتى 8 موضوعات`;
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
      $("#pollinations-key").value = values.pollinations || "";
      if (values.provider) {
        const radio = $(`input[name="provider"][value="${values.provider}"]`);
        if (radio) radio.checked = true;
      }
      $("#video-language").value = values.video_language === "ar" ? "ar" : "en";
      $("#duration-minutes").value = [1, 3, 5, 10].includes(Number(values.duration_minutes)) ? String(values.duration_minutes) : "1";
      updateVoiceOptions(values.voice || "");
      updateDurationLabel();
      updateProviderHint();
    } catch (_) { /* Ignore invalid browser-local preferences. */ }
  }

  function saveSettings(showToast = true) {
    const values = {
      ...keysFromInputs(),
      provider: selectedProvider(),
      video_language: $("#video-language").value,
      duration_minutes: Number($("#duration-minutes").value),
      voice: $("#voice").value,
    };
    localStorage.setItem("docuflow.keys.v1", JSON.stringify(values));
    $("#settings-feedback").textContent = "حُفظت الإعدادات في هذا المتصفح.";
    if (showToast) toast("حُفظت إعدادات هذا المتصفح.");
    setTimeout(() => { $("#settings-feedback").textContent = ""; }, 3200);
  }

  function selectedProvider() { return $('input[name="provider"]:checked').value; }

  function updateProviderHint() {
    const provider = selectedProvider();
    const note = $("#provider-hint");
    const language = $("#video-language").value === "ar" ? "العربية" : "الإنجليزية";
    const minutes = Number($("#duration-minutes").value || 1);
    const keys = keysFromInputs();
    const visuals = keys.pixabay
      ? "سيجرب Pixabay أولاً، ثم صورة Pollinations بعد موافقتك؛ وإلا يستخدم خلفية متحركة محلية."
      : "بدون Pixabay يستخدم خلفية محلية متحركة، أو صورة Pollinations بمفتاح وموافقة صريحة.";
    if (provider === "demo") note.lastElementChild.textContent = minutes > 1
      ? `وضع التجربة يدعم دقيقة واحدة فقط؛ اختر Groq أو Gemini أو Pollinations لفيديو ${minutes} دقائق. ${visuals}`
      : `الوضع التجريبي بلا خدمة كتابة خارجية؛ ينشئ 16 مشهداً باللغة ${language} لاختبار دقيقة واحدة والصوت والحركة والترجمة. ${visuals}`;
    if (provider === "groq") note.lastElementChild.textContent = keys.groq ? `سيُنشأ السيناريو باللغة ${language} عبر Groq. ${visuals}` : "أضف مفتاح Groq من صفحة الإعدادات قبل البدء. يمكن استخدام فيديو Pixabay أو خلفيات متحركة محلية.";
    if (provider === "gemini") note.lastElementChild.textContent = keys.gemini ? `سيقرأ التطبيق models.list من Google، ثم يختار نموذج generateContent نشطاً ويجرب البديل عند تعذره. ${visuals}` : "أضف مفتاح Gemini من صفحة الإعدادات قبل البدء؛ يكتشف التطبيق النماذج النشطة بدلاً من اسم ثابت.";
    if (provider === "pollinations") note.lastElementChild.textContent = keys.pollinations ? `سيستخدم كتالوج Pollinations الحالي للنص. يتطلب المفتاح وقد يستهلك Pollen؛ فعّل مربع الموافقة لكل تشغيل. ${visuals}` : "أضف Pollinations API Key من الإعدادات؛ الخدمة تتطلب مفتاحاً وقد تستهلك Pollen، وليست keyless حالياً.";
    const consent = $("#pollinations-consent");
    consent.classList.toggle("is-confirmed", $("#allow-pollinations").checked);
    consent.classList.toggle("is-required", provider === "pollinations" || $("#allow-pollinations").checked);
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
    const language = result.video_language === "ar" ? "عربي" : "English";
    const sceneCount = Number(result.scene_count || (result.scenes || []).length || 0);
    $("#result-meta").textContent = `${language} · ${result.aspect_ratio || "16:9"} · ${mins}:${String(secs).padStart(2, "0")} · ${sceneCount} مشهداً · ${result.voice || "edge-tts"} · ${result.script_model || "demo"}`;
    $("#result-source").textContent = `${result.demo ? "وضع تجريبي · " : ""}${result.visual_source || "مرئيات متحركة"}`;
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
    const allowPollinations = $("#allow-pollinations").checked;
    const pollinationsKey = $("#pollinations-key").value.trim();
    if (provider === "pollinations" && !pollinationsKey) { navigate("settings"); toast("أضف Pollinations API Key أولاً.", true); return; }
    if (provider === "pollinations" && !allowPollinations) { toast("فعّل مربع موافقة Pollinations لتأكيد احتمال استهلاك رصيد Pollen.", true); return; }
    if (allowPollinations && !pollinationsKey) { navigate("settings"); toast("أضف Pollinations API Key أو ألغِ موافقة توليد الصور.", true); return; }
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
          duration_minutes: Number($("#duration-minutes").value),
          video_language: $("#video-language").value,
          voice: $("#voice").value,
          provider,
          allow_pollinations: allowPollinations,
          keys: keysFromInputs(),
          whisper_model: "tiny",
          background_music: true,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "تعذر بدء الإنتاج.");
      if (allowPollinations) { $("#allow-pollinations").checked = false; updateProviderHint(); }
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
        const language = item.video_language === "ar" ? "عربي" : "English";
        meta.textContent = `${date} · ${language} · ${item.aspect_ratio || "16:9"} · ${mb} MB`;
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
  $("#pollinations-backup-button").addEventListener("click", () => {
    const radio = $('input[name="provider"][value="pollinations"]');
    radio.checked = true;
    updateProviderHint();
    saveSettings(false);
    if (!keysFromInputs().pollinations) {
      navigate("settings");
      toast("أضف Pollinations API Key أولاً؛ التوليد يتطلب مفتاحاً ورصيداً.", true);
    } else if (!$("#allow-pollinations").checked) {
      $("#pollinations-consent").scrollIntoView({ behavior: "smooth", block: "center" });
      toast("راجع التنبيه ثم وافق صراحةً إذا أردت استخدام Pollinations.");
    }
  });
  $("#show-history").addEventListener("click", () => navigate("history"));
  $$('[data-open-studio]').forEach(button => button.addEventListener("click", () => navigate("studio")));
  $$(".ratio-option").forEach(button => button.addEventListener("click", () => {
    $$(".ratio-option").forEach(option => option.classList.remove("selected"));
    button.classList.add("selected"); aspectRatio = button.dataset.ratio;
  }));
  $$('input[name="provider"]').forEach(radio => radio.addEventListener("change", () => { updateProviderHint(); saveSettings(false); }));
  keyIds.forEach(id => $(`#${id}`).addEventListener("input", updateProviderHint));
  $("#video-language").addEventListener("change", () => {
    updateVoiceOptions();
    updateProviderHint();
    saveSettings(false);
  });
  $("#duration-minutes").addEventListener("change", () => {
    updateDurationLabel();
    updateProviderHint();
    saveSettings(false);
  });
  $("#voice").addEventListener("change", () => saveSettings(false));
  $("#allow-pollinations").addEventListener("change", updateProviderHint);
  $("#save-settings").addEventListener("click", () => saveSettings(true));
  $("#clear-settings").addEventListener("click", () => {
    keyIds.forEach(id => { $(`#${id}`).value = ""; });
    $("#allow-pollinations").checked = false;
    saveSettings(false);
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
