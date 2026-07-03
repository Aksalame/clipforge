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
    unlock("step-transcribe");
    $("transcribeBtn").disabled = false;
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
  `;
  grid.appendChild(div);
}
