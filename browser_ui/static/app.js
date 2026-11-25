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
async function api(path, options = {}) {
  const r = await fetch("/api/" + path, {
    ...options,
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
  });
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
  $("send").disabled = $("message").disabled = !ready;
  for (const id of ["projects", "new", "history"])
    $(id).disabled = !ready || !q.chat;
  for (const b of $("choices").children) b.disabled = !ready;
  for (const b of $("toolList").querySelectorAll("button"))
    b.disabled = !ready || !q.chat;
}
async function send(text, id = state?.question?.id) {
  if (!id || pending || !connected) return;
  pending = true;
  controls();
  $("alert").textContent = "";
  try {
    await api("input", { method: "POST", body: JSON.stringify({ id, text }) });
    if (state?.question?.id === id) state.question = null;
    if ($("message").value === text || text === "/swarm " + $("message").value)
      $("message").value = "";
  } catch (e) {
    $("alert").textContent = e.message;
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
function entry(row, expanded) {
  const node = el("article", "", "entry " + row.kind);
  node.dataset.id = row.id;
  if (row.kind === "tool") {
    const details = document.createElement("details"), summary = document.createElement("summary");
    details.open = expanded;
    summary.append(el("strong", row.label), el("span", row.status, "toolStatus"));
    if (row.elapsed != null) summary.append(el("small", row.elapsed.toFixed(2) + " s"));
    if (row.status === "Failed") node.classList.add("failed");
    details.append(summary);
    for (const [key, title] of [["arguments", "Arguments"], ["result", "Result"]]) {
      if (row[key] !== undefined && row[key] !== "") details.append(el("h4", title), el("pre", formatted(row[key])));
    }
    node.append(details);
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
    const node = entry(row, !!old?.node.querySelector("details")?.open);
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
    if (current === "settings") loadCredential();
    if (current === "skills") query();
  };
$("search").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(query, 180);
};
$("group").onchange = () => query();
$("more").onclick = () => query(true);
$("composer").onsubmit = (e) => {
  e.preventDefault();
  if (state?.question?.chat && !$("message").value.trim()) return;
  const text = $("message").value;
  send(
    state?.question?.chat &&
      !state.swarm &&
      $("runMode").value === "swarm" &&
      !text.startsWith("/")
      ? "/swarm " + text
      : text,
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
