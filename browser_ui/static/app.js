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
function assistantText(source) {
  const container = el("div", "", "answer");
  const parts = source.split(/^```[^\n]*$/m);
  parts.forEach((part, index) => {
    if (index % 2) {
      container.append(el("pre", part.replace(/^\n|\n$/g, "")));
      return;
    }
    for (const line of part.split("\n")) {
      const heading = line.match(/^#{1,6} (.+)/),
        node = el(heading ? "h4" : "p", "");
      const body = heading ? heading[1] : line.replace(/^[-*] /, "• ");
      for (const fragment of body.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
        if (fragment.startsWith("**") && fragment.endsWith("**"))
          node.append(el("strong", fragment.slice(2, -2)));
        else if (fragment.startsWith("`") && fragment.endsWith("`"))
          node.append(el("code", fragment.slice(1, -1)));
        else node.append(document.createTextNode(fragment));
      }
      if (body) container.append(node);
    }
  });
  return container;
}
function render(s) {
  const previous = state?.question?.id;
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
  const rows = s.rows.map((row) => {
    const n = el("article", "", "entry " + row.kind);
    n.append(
      el(
        "small",
        row.label ||
          {
            user: "You",
            assistant: s.agent,
            console: "Console",
            error: "Error",
            notice: "Session",
            tool: "Result",
            activity: "Activity",
          }[row.kind] ||
          row.kind,
      ),
    );
    n.append(
      row.kind === "assistant"
        ? assistantText(row.text)
        : el(row.kind === "console" ? "pre" : "p", row.text),
    );
    return n;
  });
  feed.replaceChildren(
    ...(rows.length
      ? rows
      : [
          el(
            "p",
            s.question?.chat
              ? "Send a task to start this conversation."
              : "Choose a session option below.",
            "empty",
          ),
        ]),
  );
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
      else if (state) render(state);
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
    for (const view of ["session", "skills", "tools"])
      $(view).hidden = view !== current;
    for (const other of document.querySelectorAll("[data-view]"))
      other.removeAttribute("aria-current");
    b.setAttribute("aria-current", "page");
    $("crumb").textContent = b.textContent.slice(1).trim();
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
  ["history", "/history"],
])
  $(id).onclick = () => send(cmd);
poll();
