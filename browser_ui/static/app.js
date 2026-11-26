"use strict";
const $ = (id) => document.getElementById(id);
const token =
  location.hash.slice(1) || sessionStorage.getItem("agnes-key") || "";
if (location.hash) {
  sessionStorage.setItem("agnes-key", token);
  history.replaceState(null, "", location.pathname);
}
let state,
  pending = false,
  connected = false,
  current = "session",
  sequence = 0,
  searchTimer,
  next = null,
  skillItems = [];
const el = (tag, txt, cls) => {
  const n = document.createElement(tag);
  n.textContent = txt;
  if (cls) n.className = cls;
  return n;
};
const themeControl = $("theme");
try {
  const saved = localStorage.getItem("agnes-theme");
  if (["light", "dark"].includes(saved)) themeControl.value = saved;
} catch { /* Browser storage is optional. */ }
function applyTheme() {
  document.documentElement.dataset.theme = themeControl.value;
}
applyTheme();
themeControl.onchange = () => {
  applyTheme();
  try { localStorage.setItem("agnes-theme", themeControl.value); } catch {}
};

async function api(path, options = {}) {
  const r = await fetch("/api/" + path, {
    ...options,
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
  });
  if (!r.ok && path === "settings/models" && r.status === 400) throw new Error(await r.text());
  if (!r.ok)
    throw new Error(
      r.status === 403
        ? "Open the private workspace URL printed in your terminal."
        : r.status === 409
          ? "The session has already moved to another prompt."
          : "Request failed (" + r.status + ").",
    );
  return r.status === 204 ? null : r.json();
}
function button(label, action, cls = "subtle") {
  const b = el("button", label, cls);
  b.type = "button";
  b.onclick = action;
  return b;
}
function controls() {
  const q = state?.question,
    ready = !!q && !pending && connected && !state.done;
  $("send").textContent = pending ? "Sending…" : "Send";
  $("send").disabled = $("message").disabled = !ready;
  $("documentControl").hidden = !q?.chat;
  $("documentFile").disabled = $("removeDocument").disabled = !ready;
  for (const id of ["projects", "new", "history"])
    $(id).disabled = !ready || !q.chat;
  for (const b of $("choices").children) b.disabled = !ready;
  for (const b of $("toolList").querySelectorAll("button"))
    b.disabled = !ready || !q.chat;
}
async function send(text, id = state?.question?.id, file = null) {
  if (!id || pending || !connected) return;
  pending = true;
  controls();
  $("alert").textContent = "";
  try {
    let content = text;
    if (file) {
      if (file.size > 5 * 1024 ** 2) throw new Error("Choose a file under 5 MiB.");
      const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      const response = await fetch("/api/document?extension=" + encodeURIComponent(extension), {
        method: "POST", headers: { Authorization: "Bearer " + token }, body: file
      });
      if (!response.ok) throw new Error(await response.text());
      const document = await response.json();
      content += "\n\nAttached document " + JSON.stringify(file.name) + "\n" + document.text;
      if (Array.from(content).length > 32000) throw new Error("Your message and document exceed 32,000 characters. Shorten one and try again.");
    }
    await api("input", { method: "POST", body: JSON.stringify({ id, text: content }) });
    if (file) {
      resetDocument();
      $("message").value = "";
    }
    if (state?.question?.id === id) state.question = null;
    if ($("message").value === text || text === "/swarm " + $("message").value)
      $("message").value = "";
  } catch (e) {
    $(file ? "documentError" : "alert").textContent = e.message;
  } finally {
    pending = false;
    controls();
  }
}
function markdownBody(html) {
  const body = el("div", "", "answer");
  // HTML is produced by the local CommonMark renderer with raw HTML disabled.
  body.innerHTML = html;
  for (const link of body.querySelectorAll("a[href]")) {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  for (const table of body.querySelectorAll("table")) {
    const wrap = el("div", "", "tableScroll");
    wrap.tabIndex = 0;
    table.replaceWith(wrap);
    wrap.append(table);
  }
  for (const cell of body.querySelectorAll("td[style],th[style]")) {
    cell.className = "align-" + cell.style.textAlign;
    cell.removeAttribute("style");
  }
  for (const pre of body.querySelectorAll("pre")) {
    const code = pre.querySelector("code");
    if (!code) continue;
    const wrap = el("section", "", "codeBlock"), bar = el("div", "", "codeBar");
    const copy = button("Copy", async () => {
      try {
        await navigator.clipboard.writeText(code.textContent);
        copy.textContent = "Copied";
      } catch { copy.textContent = "Copy unavailable"; }
    });
    bar.append(el("span", code.className.replace(/^language-/, "") || "Code"), copy);
    pre.replaceWith(wrap);
    wrap.append(bar, pre);
  }
  for (const li of body.querySelectorAll("li")) {
    const first = li.firstElementChild?.tagName === "P" ? li.firstElementChild : li;
    const text = first.firstChild;
    const match = text?.nodeType === Node.TEXT_NODE && /^\[([ xX])\]\s+/.exec(text.data);
    if (!match) continue;
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = match[1] !== " ";
    box.disabled = true;
    text.deleteData(0, match[0].length);
    first.prepend(box, " ");
    li.className = "taskItem";
  }
  return body;
}
function formatted(value) {
  if (typeof value === "string") {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
  }
  return JSON.stringify(value, null, 2);
}
function objectValue(value) {
  if (typeof value === "string") {
    try { value = JSON.parse(value); } catch { return null; }
  }
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}
function renderWorkspaceWrite(node, row) {
  const args = objectValue(row.arguments);
  const write = args?.action === "write" && typeof args.path === "string" && typeof args.content === "string";
  let panel = node.querySelector(".writePanel");
  if (!write) {
    if (panel) {
      panel.after(node.querySelector(".arguments"), node.querySelector(".result"));
      panel.remove();
    }
    return;
  }
  if (!panel) {
    panel = el("section", "", "writePanel");
    const raw = el("details", "", "rawWrite");
    raw.append(el("summary", "Raw JSON"));
    node.querySelector(".arguments").before(panel);
    raw.append(node.querySelector(".arguments"), node.querySelector(".result"));
    panel.append(el("p", "", "writeOutcome"), el("pre", "", "writeSource"), raw);
  }
  const result = objectValue(row.result);
  const success = row.status === "Completed" && !result?.error && Number.isSafeInteger(result?.bytes);
  const path = (success && typeof result.path === "string") ? result.path : args.path;
  node.querySelector("summary strong").textContent = (success ? "Wrote " : "Write ") + path;
  const outcome = panel.querySelector(".writeOutcome");
  outcome.textContent = success ? result.bytes.toLocaleString() + " bytes written"
    : row.status === "Failed" ? String(result?.message || result?.error || "Write failed") : "";
  outcome.hidden = !outcome.textContent;
  panel.querySelector(".writeSource").textContent = args.content;
}
function renderTerminal(node, row) {
  const args = objectValue(row.arguments) || {}, result = objectValue(row.result) || {};
  let panel = node.querySelector(".terminalPanel");
  if (!panel) {
    panel = el("section", "", "terminalPanel");
    const raw = el("details", "", "rawWrite");
    raw.append(el("summary", "Raw JSON"));
    node.querySelector(".arguments").before(panel);
    raw.append(node.querySelector(".arguments"), node.querySelector(".result"));
    panel.append(el("dl", "", "terminalFacts"), el("p", "", "terminalNotice"),
      el("div", "", "terminalProcesses"), node.querySelector(".stdout"), node.querySelector(".stderr"),
      node.querySelector(".observation"), raw);
  }
  const action = args.action || "run";
  const titles = {list:"List processes", get:"Inspect process", wait:"Wait for process", kill:"Stop process"};
  const command = args.command || result.command || titles[action] || "Terminal";
  const heading = node.querySelector("summary strong");
  heading.classList.add("terminalCommand");
  heading.textContent = command;
  heading.title = command;
  const duration = result.duration ?? row.elapsed;
  node.querySelector("summary small").textContent = Number.isFinite(duration) ? duration + " s" : "";
  const facts = panel.querySelector(".terminalFacts");
  facts.replaceChildren();
  for (const [label, value] of [["Directory", result.cwd ?? args.cwd], ["Timeout", args.timeout == null ? null : args.timeout + " s"],
    ["Process ID", result.process_id ?? result.id ?? args.process_id], ["Exit code", result.exit_code]]) {
    if (value != null) facts.append(el("dt", label), el("dd", String(value)));
  }
  facts.hidden = !facts.childElementCount;
  for (const key of ["stdout", "stderr"]) {
    const section = panel.querySelector("." + key);
    const output = result[key] || row[key] || "";
    section.hidden = !output;
    section.querySelector("pre").textContent = output.replace(/\r\n?/g, "\n");
  }
  const notice = panel.querySelector(".terminalNotice");
  const messages = [];
  if (result.error) messages.push(String(result.message || result.error));
  if (result.truncated || (typeof row.result === "string" && row.result.endsWith("[truncated]"))) messages.push("Output truncated.");
  if (action === "list" && Array.isArray(result.processes) && !result.processes.length) messages.push("No processes.");
  notice.textContent = messages.join(" ");
  notice.hidden = !messages.length;
  const processes = panel.querySelector(".terminalProcesses");
  processes.replaceChildren();
  for (const process of result.processes || []) {
    processes.append(el("p", [process.id, process.command, process.status].filter(Boolean).join(" · ")));
  }
}
function updateStep(node, row) {
  const summary = node.querySelector("summary");
  if (row.kind === "thinking") {
    summary.querySelector(".preview").textContent = row.text;
    node.querySelector(".stepText").textContent = row.text;
    return;
  }
  summary.querySelector("strong").textContent = row.label;
  summary.querySelector(".toolStatus").textContent = row.status;
  summary.querySelector("small").textContent = row.elapsed == null ? "" : row.elapsed.toFixed(2) + " s";
  node.classList.toggle("failed", ["Failed", "Interrupted"].includes(row.status));
  for (const key of ["arguments", "stdout", "stderr", "observation", "result"]) {
    const section = node.querySelector("." + key);
    section.hidden = row[key] === undefined;
    section.querySelector("pre").textContent = (key === "arguments" || key === "result" ? formatted(row[key]) : row[key]) ?? "";
  }
  if (row.label === "workspace") renderWorkspaceWrite(node, row);
  if (row.label === "terminal") renderTerminal(node, row);
}
function entry(row) {
  const node = el("article", "", "entry " + row.kind);
  node.dataset.id = row.id;
  if (row.kind === "tool" || row.kind === "thinking") {
    const details = el("details", ""), summary = el("summary", "");
    const icon = el("span", "▸", "stepIcon");
    icon.setAttribute("aria-hidden", "true");
    summary.append(icon);
    details.append(summary);
    if (row.kind === "tool") {
      summary.append(el("strong", ""), el("span", "", "toolStatus"), el("small", ""));
      for (const [key, title] of [["arguments", "Arguments"], ["stdout", "Standard output"], ["stderr", "Standard error"], ["observation", "Latest model update"], ["result", "Result"]]) {
        const section = el("section", "", key);
        section.append(el("h4", title), el("pre", ""));
        details.append(section);
      }
    } else {
      summary.append(el("strong", "Thinking"), el("span", "", "preview"));
      details.append(el("p", "", "stepText"));
    }
    node.append(details);
    updateStep(node, row);
  } else {
    const labels = {user:"You", assistant:state.agent, console:"Console", error:"Error", notice:"Session", activity:"Activity"};
    node.append(el("small", row.label || labels[row.kind] || row.kind));
    node.append(row.html !== undefined ? markdownBody(row.html) : el(row.kind === "console" ? "pre" : "p", row.text));
  }
  return node;
}
const rendered = new Map();
let firstAvailable, hasOlder;
function drawRows(rows, prepend = false) {
  const feed = $("feed");
  for (const row of prepend ? [...rows].reverse() : rows) {
    const key = JSON.stringify(row), old = rendered.get(row.id);
    if (old?.key === key) continue;
    if (old && (row.kind === "thinking" || row.kind === "tool")) {
      updateStep(old.node, row);
      old.key = key;
      continue;
    }
    const node = entry(row);
    if (old) old.node.replaceWith(node);
    else if (prepend) feed.prepend(node);
    else feed.append(node);
    rendered.set(row.id, {key, node});
  }
}
function render(s) {
  const previous = state?.question?.id;
  if (current === "settings" && !savingCredential && $("credentialName").value === "provider"
      && (state?.needsCredential !== s.needsCredential || state?.provider !== s.provider)) loadCredential();
  if (previous !== s.question?.id) resetDocument();
  state = s;
  $("project").textContent = s.project;
  $("context").textContent = s.agent + " · " + s.model;
  $("provider").textContent = s.provider;
  $("mode").textContent = s.swarm ? "Swarm" : "Single agent";
  $("tokens").textContent = s.tokens.toLocaleString();
  $("connection").textContent = s.done
    ? "Session ended"
    : s.question
      ? "Awaiting input"
      : "Working";
  $("skillCount").textContent =
    s.skills.toLocaleString() + " skills available in the installed runtime.";
  const feed = $("feed"),
    atEnd = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 48;
  if (s.firstId !== firstAvailable || (s.hasOlder && !s.rows.some(row => rendered.has(row.id)))) {
    feed.replaceChildren();
    rendered.clear();
    firstAvailable = s.firstId;
    hasOlder = s.hasOlder;
  }
  feed.querySelector(".empty")?.remove();
  drawRows(s.rows);
  if (!s.rows.length) feed.append(el("p", s.question?.chat ? "Send a task to start this conversation." : "Choose a session option below.", "empty"));
  $("earlier").hidden = !hasOlder;
  if (atEnd) feed.scrollTop = feed.scrollHeight;
  const q = s.question;
  $("runMode").hidden = !q?.chat || s.swarm;
  $("question").textContent =
    q?.label || (s.done ? "Session ended" : "Agent is working");
  $("message").placeholder = q?.default
    ? "Default: " + q.default
    : q?.chat
      ? "Describe a task or enter a slash command…"
      : q
        ? "Enter your response…"
        : "Waiting for the agent…";
  if (previous !== q?.id) {
    $("choices").replaceChildren(
      ...(q?.choices || []).map((x) =>
        button(x.name, () => send(x.value, q.id), "choice"),
      ),
    );
  }
  $("toolList").replaceChildren(
    ...(s.tools.length
      ? s.tools.map((t) => {
          const n = el("div", "", "toolRow");
          n.append(
            el("code", t.name),
            button(t.enabled ? "Enabled" : "Disabled", () =>
              send("/tools " + (t.enabled ? "off " : "on ") + t.name),
            ),
          );
          return n;
        })
      : [el("p", "Open a session to manage its tools.", "empty")]),
  );
  controls();
}
async function poll() {
  while (true) {
    try {
      const s = await api("state?since=" + (state?.revision ?? -1));
      connected = true;
      if (s) render(s);
      else controls();
      $("alert").textContent = "";
      if (state?.done) break;
    } catch (e) {
      connected = false;
      controls();
      $("connection").textContent = "Disconnected";
      $("alert").textContent = e.message;
      if (!token) break;
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}
async function query(append = false) {
  const serial = ++sequence,
    q = $("search").value.trim(),
    group = $("group").value;
  const params = new URLSearchParams({
    q,
    offset: String(append ? next || 0 : 0),
  });
  if (group) params.set("group", group);
  try {
    const data = await api("skills?" + params);
    if (serial !== sequence) return;
    if (data.groups) {
      $("group").replaceChildren(
        new Option("All groups", ""),
        ...Object.keys(data.groups).map((x) => new Option(x, x)),
      );
      $("results").replaceChildren(
        ...Object.entries(data.groups).map(([g, n]) =>
          button(
            g + " · " + n,
            () => {
              $("group").value = g;
              query();
            },
            "choice",
          ),
        ),
      );
      skillItems = [];
      next = null;
    } else {
      skillItems = (append ? skillItems : []).concat(
        Object.entries(data.skills || {}),
      );
      next = data.next_offset;
      $("results").replaceChildren(
        ...(skillItems.length
          ? skillItems.map(([name, desc]) => {
              const n = el("div", "", "skill");
              n.append(el("code", name), el("p", desc));
              return n;
            })
          : [el("p", "No matching skills.", "empty")]),
      );
    }
    $("more").hidden = next == null;
  } catch (e) {
    $("alert").textContent = e.message;
  }
}
for (const b of document.querySelectorAll("[data-view]"))
  b.onclick = () => {
    current = b.dataset.view;
    for (const view of ["session", "skills", "tools", "settings"])
      $(view).hidden = view !== current;
    for (const other of document.querySelectorAll("[data-view]"))
      other.removeAttribute("aria-current");
    b.setAttribute("aria-current", "page");
    $("crumb").textContent = b.lastChild.textContent.trim();
    $("credentialValue").value = "";
    if (current === "settings") { loadCredential(); loadModels(); }
    if (current === "skills") query();
  };
$("search").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(query, 180);
};
$("group").onchange = () => query();
$("more").onclick = () => query(true);
function resetDocument() {
  $("documentName").textContent = "";
  $("documentFile").value = "";
  $("removeDocument").hidden = true;
  $("documentError").textContent = "";
}
$("documentFile").onchange = () => {
  $("documentName").textContent = $("documentFile").files[0]?.name || "";
  $("removeDocument").hidden = !$("documentFile").files.length;
  $("documentError").textContent = "";
};
$("removeDocument").onclick = resetDocument;
$("composer").onsubmit = (e) => {
  e.preventDefault();
  const file = state?.question?.chat && $("documentFile").files[0];
  const text = $("message").value || (file ? "Read the attached document." : "");
  if (state?.question?.chat && !text.trim()) return;
  if (file && text.trimStart().startsWith("/")) {
    $("documentError").textContent = "Send a chat message with the attachment. Select Swarm from Run mode for a swarm task.";
    return;
  }
  send(
    state?.question?.chat &&
      !state.swarm &&
      $("runMode").value === "swarm" &&
      !text.startsWith("/")
      ? "/swarm " + text
      : text,
    state?.question?.id, file,
  );
};
$("message").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
};
for (const [id, cmd] of [
  ["projects", "/projects"],
  ["new", "/new"],

])
  $(id).onclick = () => send(cmd);
$("earlier").onclick = async () => {
  const feed = $("feed"), earliest = feed.firstElementChild, first = firstAvailable;
  $("earlier").disabled = true;
  try {
    const batch = await api("earlier?before=" + earliest.dataset.id);
    if (first !== firstAvailable) return;
    const height = feed.scrollHeight;
    drawRows(batch.rows, true);
    feed.scrollTop += feed.scrollHeight - height;
    hasOlder = batch.hasOlder;
    $("earlier").hidden = !hasOlder;
  } catch (e) { $("alert").textContent = e.message; }
  finally { $("earlier").disabled = false; }
};
$("closeArchive").onclick = () => $("archive").close();
$("history").onclick = async () => {
  const prompt = state.question.id;
  $("archives").replaceChildren(el("p", "Loading conversations…"));
  $("archive").showModal();
  try {
    const entries = await api("history");
    $("archives").replaceChildren(...entries.map(item => {
      const b = button("", () => { $("archive").close(); send("/resume " + item.id, prompt); }, "archiveRow");
      b.append(el("strong", item.title || "Untitled conversation"), el("small", item.messages + " messages · " + new Date(item.updated_at * 1000).toLocaleString()));
      return b;
    }));
    if (!entries.length) $("archives").append(el("p", "No archived conversations yet.", "empty"));
  } catch (e) { $("archives").replaceChildren(el("p", e.message)); }
};
poll();


let credentialInfo, credentialRequest = 0, savingCredential = false;
async function loadCredential(message = "") {
  const serial = ++credentialRequest, name = $("credentialName").value;
  credentialInfo = null;
  $("credentialValue").value = "";
  $("saveCredential").disabled = true;
  $("forgetCredential").hidden = true;
  $("credentialTarget").textContent = "";
  $("credentialState").textContent = "";
  $("credentialNotice").textContent = "Loading…";
  try {
    const info = await api("settings/" + name);
    if (serial !== credentialRequest) return;
    credentialInfo = info;
    $("credentialTarget").textContent = info.label + " · " + info.endpoint;
    const status = info.session ? "A session key is set." : info.persistent ? "A key is saved on this device." : "No workspace key stored.";
    $("credentialState").textContent = status + (name === "provider"
      ? " Command-line and environment keys take priority. Saved changes apply at startup."
      : " Changes apply to the web-search integration.");
    const modes = $("credentialMode");
    modes.options[0].disabled = name === "provider" && !info.waiting;
    modes.options[1].disabled = !info.available;
    modes.value = modes.options[0].disabled ? "save" : "session";
    $("saveCredential").disabled = modes.selectedOptions[0].disabled;
    $("forgetCredential").hidden = !info.persistent && !info.session;
    $("forgetCredential").textContent = info.session ? "Clear session key" : "Remove saved key";
    $("credentialNotice").textContent = message || (!info.available ? "OS credential store unavailable." : "");
  } catch {
    if (serial === credentialRequest) $("credentialNotice").textContent = "Could not load this credential. Check the connection and endpoint.";
  }
}
async function changeCredential(action) {
  if (savingCredential || !credentialInfo) return;
  const name = $("credentialName").value;
  const payload = {action, endpoint:credentialInfo.endpoint, label:credentialInfo.label};
  if (action === "save" || action === "session") payload.value = $("credentialValue").value;
  $("credentialValue").value = "";
  savingCredential = true;
  for (const id of ["credentialName", "saveCredential", "forgetCredential"]) $(id).disabled = true;
  let message;
  try {
    await api("settings/" + name, {method:"POST", body:JSON.stringify(payload)});
    message = action === "save" ? "Key saved." : action === "session" ? "Session key applied." : "Key removed.";
  } catch {
    message = "Change failed. Check the key, endpoint and credential store.";
  } finally {
    delete payload.value;
    await loadCredential(message);
    savingCredential = false;
    $("credentialName").disabled = $("forgetCredential").disabled = false;
  }
}
$("credentialName").onchange = () => loadCredential();
$("credentialForm").onsubmit = event => {
  event.preventDefault();
  changeCredential($("credentialMode").value);
};
$("forgetCredential").onclick = () => changeCredential(credentialInfo.session ? "clear" : "forget");
function applyTextSize(size) {
  $("textSize").value = size === "large" ? "large" : "standard";
  document.documentElement.classList.toggle("largeMessages", $("textSize").value === "large");
}
try { applyTextSize(localStorage.getItem("agnes-text-size")); } catch { applyTextSize("standard"); }
$("textSize").onchange = () => {
  applyTextSize($("textSize").value);
  try { localStorage.setItem("agnes-text-size", $("textSize").value); } catch {}
};

let modelSettings, modelBusy = false;

function showModelKeys(preferred) {
  const provider = $("modelProvider").value, info = modelSettings.providers[provider];
  $("modelEndpoint").textContent = info.endpoint;
  $("modelOptions").replaceChildren(...[...new Set([info.model, ...info.history])].map(m => new Option(m, m)));
  const keys = modelSettings.keys.filter(k => k.provider === provider && k.endpoint === info.endpoint);
  const options = keys.map(k => new Option(k.label, k.id));
  if (info.keyless) options.unshift(new Option("Local connection", "local"));
  $("modelKey").replaceChildren(...options);
  if (options.some(o => o.value === preferred)) $("modelKey").value = preferred;
  $("useModel").disabled = !options.length || ($("modelKey").value !== "local" && !modelSettings.unlocked);
}

async function loadModels(preferredProvider, preferredKey, editedModel) {
  try {
    modelSettings = await api("settings/models");
    const selected = modelSettings.selected, providers = modelSettings.providers;
    $("modelProvider").replaceChildren(...Object.entries(providers).map(([id, p]) => new Option(p.label, id)));
    const provider = preferredProvider || selected?.provider || state?.provider;
    if (providers[provider]) $("modelProvider").value = provider;
    const id = $("modelProvider").value;
    $("modelId").value = editedModel ?? (selected?.provider === id ? selected.model :
      state?.provider === id ? state.model : providers[id].model);
    showModelKeys(preferredKey || selected?.key_id);
    const activeKey = modelSettings.keys.find(k => k.id === selected?.key_id);
    $("modelSelection").textContent = selected
      ? "Selected: " + selected.provider + " / " + selected.model + " · " + (activeKey?.label || "Local connection")
      : "Choose the model and key for your next request.";
    $("unlockForm").hidden = modelSettings.unlocked;
    $("namedKeyForm").hidden = !modelSettings.unlocked;
    $("vaultLabel").textContent = modelSettings.initialized ? "Vault passphrase" : "Create a vault passphrase (12+ characters)";
    $("unlockVault").textContent = modelSettings.initialized ? "Unlock saved keys" : "Create key vault";
  } catch (e) { $("modelNotice").textContent = e.message; }
}

$("modelProvider").onchange = () => {
  const id = $("modelProvider").value;
  $("modelId").value = modelSettings.selected?.provider === id ? modelSettings.selected.model : modelSettings.providers[id].model;
  showModelKeys(modelSettings.selected?.key_id);
};
$("modelKey").onchange = () => showModelKeys($("modelKey").value);

async function updateModels(action) {
  if (modelBusy || !modelSettings) return;
  modelBusy = true;
  const provider = $("modelProvider").value, model = $("modelId").value, key_id = $("modelKey").value;
  const payload = {action, provider, model, key_id};
  if (action === "unlock") payload.passphrase = $("vaultPhrase").value;
  if (action === "save") Object.assign(payload, {label: $("namedLabel").value, value: $("namedSecret").value});
  $("modelNotice").textContent = "";
  try {
    const result = await api("settings/models", {method:"POST", body:JSON.stringify(payload)});
    $("vaultPhrase").value = $("namedSecret").value = "";
    if (action === "save") $("namedLabel").value = "";
    await loadModels(provider, result.key_id || key_id, model);
    $("modelNotice").textContent = {unlock:"Vault unlocked.",save:"Key saved. Select it for a model above.",
      select:"Selection saved for your next request."}[action];
  } catch (e) { $("modelNotice").textContent = e.message; }
  finally { modelBusy = false; }
}
$("useModel").onclick = () => updateModels("select");
$("unlockForm").onsubmit = e => { e.preventDefault(); updateModels("unlock"); };
$("namedKeyForm").onsubmit = e => { e.preventDefault(); updateModels("save"); };
