let jobId = null;
let selectedFile = null;
let highlightPicks = [];

const $ = (id) => document.getElementById(id);

function unlock(cardId) {
  $(cardId).classList.remove("disabled");
}

function setStatus(id, msg, isError = false) {
  const el = $(id);
  el.textContent = msg;
  el.className = "status" + (isError ? " error" : "");
}

// --- Upload -----------------------------------------------------------

$("browseBtn").addEventListener("click", () => $("fileInput").click());

$("dropzone").addEventListener("dragover", (e) => e.preventDefault());
$("dropzone").addEventListener("drop", (e) => {
  e.preventDefault();
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});
$("fileInput").addEventListener("change", (e) => {
  if (e.target.files.length) handleFile(e.target.files[0]);
});

function handleFile(file) {
  selectedFile = file;
  $("fileInfo").textContent = `${file.name} — ${(file.size / 1e6).toFixed(1)} MB`;
  $("uploadBtn").disabled = false;
}

function onVideoReady(data) {
  jobId = data.job_id;
  unlock("step-transcribe");
  $("transcribeBtn").disabled = false;
}

$("fetchYoutubeBtn").addEventListener("click", async () => {
  const url = $("youtubeUrl").value.trim();
  if (!url) return;
  $("fetchYoutubeBtn").disabled = true;
  $("fetchYoutubeBtn").textContent = "Fetching...";
  setStatus("uploadStatus", "Downloading from YouTube (can take a bit for longer videos)...");
  try {
    const res = await fetch("/api/fetch-youtube", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    setStatus("uploadStatus", `Fetched — ${data.duration.toFixed(0)}s, ${data.width}x${data.height}`);
    onVideoReady(data);
  } catch (e) {
    setStatus("uploadStatus", "Failed: " + e.message, true);
  } finally {
    $("fetchYoutubeBtn").disabled = false;
    $("fetchYoutubeBtn").textContent = "Fetch from YouTube";
  }
});

$("uploadBtn").addEventListener("click", async () => {
  if (!selectedFile) return;
  $("uploadBtn").disabled = true;
  $("uploadBtn").textContent = "Uploading...";
  const fd = new FormData();
  fd.append("video", selectedFile);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    jobId = data.job_id;
    $("uploadBtn").textContent = `Uploaded (${data.duration.toFixed(0)}s, ${data.width}x${data.height})`;
    onVideoReady(data);
  } catch (e) {
    $("uploadBtn").textContent = "Upload";
    $("uploadBtn").disabled = false;
    alert("Upload failed: " + e.message);
  }
});

// --- Transcribe ---------------------------------------------------------

$("transcribeBtn").addEventListener("click", async () => {
  const openai_key = $("openai_key").value.trim();
  if (!openai_key) {
    setStatus("transcribeStatus", "Enter your OpenAI API key above first.", true);
    return;
  }
  $("transcribeBtn").disabled = true;
  setStatus("transcribeStatus", "Extracting audio and transcribing (this can take a minute)...");
  try {
    const res = await fetch("/api/transcribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, openai_key }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    setStatus("transcribeStatus", `Transcribed — ${data.word_count} words captured.`);
    unlock("step-highlights");
    $("highlightsBtn").disabled = false;
  } catch (e) {
    setStatus("transcribeStatus", "Failed: " + e.message, true);
    $("transcribeBtn").disabled = false;
  }
});

// --- Highlights -----------------------------------------------------------

$("highlightsBtn").addEventListener("click", async () => {
  const anthropic_key = $("anthropic_key").value.trim();
  if (!anthropic_key) {
    setStatus("highlightsStatus", "Enter your Anthropic API key above first.", true);
    return;
  }
  $("highlightsBtn").disabled = true;
  setStatus("highlightsStatus", "Asking Claude to pick the best moments...");
  try {
    const res = await fetch("/api/highlights", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: jobId,
        anthropic_key,
        num_clips: parseInt($("numClips").value, 10),
      }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    highlightPicks = data.highlights;
    renderHighlights();
    setStatus("highlightsStatus", `Found ${highlightPicks.length} highlight moments.`);
    unlock("step-results");
  } catch (e) {
    setStatus("highlightsStatus", "Failed: " + e.message, true);
    $("highlightsBtn").disabled = false;
  }
});

function renderHighlights() {
  const list = $("highlightsList");
  list.innerHTML = "";
  highlightPicks.forEach((h, i) => {
    const div = document.createElement("div");
    div.className = "highlight-item";
    div.innerHTML = `
      <div class="meta">
        <h4>${h.title}</h4>
        <p>${h.reason || ""}</p>
        <span class="time">${fmt(h.start)} → ${fmt(h.end)}</span>
      </div>
      <button class="primary" data-idx="${i}">Generate clip</button>
    `;
    div.querySelector("button").addEventListener("click", (e) => generateClip(i, e.target));
    list.appendChild(div);
  });
}

function fmt(s) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// --- Generate -----------------------------------------------------------

async function generateClip(idx, btn) {
  const h = highlightPicks[idx];
  btn.disabled = true;
  btn.textContent = "Rendering...";
  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: jobId,
        start: h.start,
        end: h.end,
        title: h.title,
        captions: $("optCaptions").checked,
        vertical: $("optVertical").checked,
      }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    addResultCard(data);
    btn.textContent = "Done ✓";
  } catch (e) {
    btn.textContent = "Failed";
    alert("Clip generation failed: " + e.message);
  }
}

function addResultCard(data) {
  const grid = $("resultsGrid");
  const div = document.createElement("div");
  div.className = "result-card";
  div.innerHTML = `
    <video src="${data.url}" controls></video>
    <div class="info">${data.title}</div>
    <a href="${data.url}" download>Download</a>
    <div class="schedule-box" style="padding:10px">
      <input type="datetime-local" class="sched-time" style="width:100%;margin-bottom:6px">
      <textarea class="sched-caption" placeholder="Caption..." style="width:100%;margin-bottom:6px;background:#0d0f0c;color:#eef0e6;border:1px solid #2a2e24;border-radius:6px;padding:6px;font-size:12px"></textarea>
      <label class="checkbox" style="font-size:11px"><input type="checkbox" class="sched-yt" checked> YouTube</label>
      <label class="checkbox" style="font-size:11px"><input type="checkbox" class="sched-ig" checked> Instagram</label>
      <button class="primary sched-btn" style="width:100%;margin-top:6px">Schedule post</button>
    </div>
  `;
  div.querySelector(".sched-btn").addEventListener("click", async (e) => {
    const time = div.querySelector(".sched-time").value;
    if (!time) { alert("Pick a date/time first"); return; }
    const platforms = [];
    if (div.querySelector(".sched-yt").checked) platforms.push("youtube");
    if (div.querySelector(".sched-ig").checked) platforms.push("instagram");
    const ts = new Date(time).getTime() / 1000;
    e.target.disabled = true;
    e.target.textContent = "Scheduling...";
    try {
      const res = await fetch("/api/schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clip_url: data.url,
          title: data.title,
          caption: div.querySelector(".sched-caption").value,
          platforms,
          scheduled_time: ts,
        }),
      });
      const result = await res.json();
      if (result.error) throw new Error(result.error);
      e.target.textContent = "Scheduled ✓";
      loadScheduled();
    } catch (e2) {
      alert("Scheduling failed: " + e2.message);
      e.target.disabled = false;
      e.target.textContent = "Schedule post";
    }
  });
  grid.appendChild(div);
}

// --- Connections (YouTube / Instagram) ------------------------------------

async function loadConnections() {
  try {
    const res = await fetch("/api/connections");
    const c = await res.json();
    $("connectYoutubeBtn").textContent = c.youtube_connected ? "YouTube ✓ connected" : "Connect YouTube";
    $("connectInstagramBtn").textContent = c.instagram_connected ? "Instagram ✓ connected" : "Connect Instagram";
    if (!c.youtube_configured || !c.instagram_configured) {
      $("connectionStatus").textContent =
        (!c.youtube_configured ? "YouTube API keys not set on the server. " : "") +
        (!c.instagram_configured ? "Instagram API keys not set on the server." : "");
    }
  } catch (e) { /* ignore on first load before server is ready */ }
}

$("connectYoutubeBtn").addEventListener("click", () => window.open("/auth/youtube", "_blank"));
$("connectInstagramBtn").addEventListener("click", () => window.open("/auth/instagram", "_blank"));

// --- Scheduled posts list ---------------------------------------------------

async function loadScheduled() {
  const res = await fetch("/api/scheduled");
  const data = await res.json();
  const list = $("scheduledList");
  list.innerHTML = "";
  if (!data.posts.length) {
    list.innerHTML = '<p class="hint">No scheduled posts yet.</p>';
    return;
  }
  data.posts.forEach((p) => {
    const platforms = JSON.parse(p.platforms).join(", ");
    const when = new Date(p.scheduled_time * 1000).toLocaleString();
    const div = document.createElement("div");
    div.className = "highlight-item";
    div.innerHTML = `
      <div class="meta">
        <h4>${p.title || "Short"}</h4>
        <p>${platforms} — status: ${p.status}</p>
        <span class="time">${when}</span>
      </div>
    `;
    list.appendChild(div);
  });
}

loadConnections();
loadScheduled();
setInterval(loadScheduled, 30000);
