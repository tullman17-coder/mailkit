(() => {
  const SECTIONS = [
    ["inbox", "Inbox"],
    ["saved", "Saved"],
    ["sent", "Sent"],
    ["drafts", "Drafts"],
    ["archives", "Archives"],
  ];

  const state = {
    token: tokenFromLocation(),
    base: apiBase(),
    accounts: [],
    account: null,
    mailbox: "inbox",
    messages: [],
    current: null,
    unread: false,
    flagged: false,
    replyId: null,
  };

  const $ = (id) => document.getElementById(id);
  function tokenFromLocation() {
    try {
      const search = typeof location !== "undefined" ? location.search || "" : "";
      return new URLSearchParams(search).get("token") || "";
    } catch {
      return "";
    }
  }
  function apiBase() {
    try {
      if (typeof location !== "undefined" && /^https?:$/.test(location.protocol)) {
        return location.origin;
      }
    } catch {
      /* file:// desktop shell */
    }
    return "http://127.0.0.1:8765";
  }
  const toast = (msg) => {
    const el = $("toast");
    el.textContent = msg;
    el.classList.add("open");
    setTimeout(() => el.classList.remove("open"), 2400);
  };

  async function api(method, path, body) {
    const headers = { Accept: "application/json" };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    if (body) headers["Content-Type"] = "application/json";
    const res = await fetch(state.base + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok || json.ok === false) {
      throw new Error((json.error && json.error.message) || res.statusText || "request failed");
    }
    return json.data;
  }

  function renderAccounts() {
    const root = $("accounts");
    if (!state.accounts.length) {
      root.innerHTML = `<p class="empty">Add an account with <span class="mono">mailkit accounts add</span>.</p>`;
      return;
    }
    root.innerHTML = state.accounts
      .map(
        (a) => `<button class="acc" type="button" data-id="${a.id}" aria-current="${state.account === a.id}">
          ${esc(a.name || a.address)}
          <small class="mono">${esc(a.id)} · ${esc(a.provider || "")}</small>
        </button>`
      )
      .join("");
    root.querySelectorAll(".acc").forEach((btn) =>
      btn.addEventListener("click", () => selectAccount(btn.dataset.id))
    );
  }

  function renderBoxes() {
    const root = $("boxes");
    root.innerHTML = SECTIONS.map(
      ([id, label]) =>
        `<button class="box" type="button" data-id="${id}" aria-current="${state.mailbox === id}">${label}</button>`
    ).join("");
    root.querySelectorAll(".box").forEach((btn) =>
      btn.addEventListener("click", () => selectMailbox(btn.dataset.id))
    );
  }

  function renderList() {
    const root = $("msgs");
    $("list-title").textContent = SECTIONS.find((s) => s[0] === state.mailbox)?.[1] || state.mailbox;
    if (!state.messages.length) {
      root.className = "empty";
      root.innerHTML = "No messages in this mailbox.";
      return;
    }
    root.className = "";
    root.innerHTML = `<ul class="msgs">${state.messages
      .map((m) => {
        const unread = m.unread ? "" : " off";
        const current = state.current && state.current.id === m.id;
        const starred = m.flagged ? "★ " : "";
        return `<li><button class="msg" type="button" data-id="${esc(m.id)}" data-flagged="${m.flagged ? "true" : "false"}" aria-current="${current}">
          <span class="dot${unread}" aria-hidden="true"></span>
          <span>
            <span class="subject">${starred}${esc(m.subject || "(no subject)")}</span>
            <small class="mono">${esc(m.account_id || "")} · ${esc(fromAddr(m))}</small>
          </span>
        </button></li>`;
      })
      .join("")}</ul>`;
    root.querySelectorAll(".msg").forEach((btn) =>
      btn.addEventListener("click", () => openMessage(btn.dataset.id))
    );
  }

  function fromAddr(m) {
    const row = (m.from || [])[0];
    return row ? row.address || row.name || "" : "";
  }

  function renderRead() {
    const root = $("read");
    const m = state.current;
    $("flag-btn").disabled = !m;
    $("flag-btn").textContent = m && m.flagged ? "Unstar" : "Star";
    $("flag-btn").setAttribute("aria-pressed", String(!!(m && m.flagged)));
    $("archive-btn").disabled = !m;
    $("reply-btn").disabled = !m;
    if (!m) {
      root.innerHTML = `<p class="empty">Account → mailbox → message. Nothing is bundled out of sight.</p>`;
      return;
    }
    root.innerHTML = `
      <h1>${esc(m.subject || "(no subject)")}</h1>
      <p class="meta mono">${esc(m.account_id)} · ${esc(m.mailbox)} · ${esc(fromAddr(m))}</p>
      <div class="body">${esc(m.body_text || m.snippet || "")}</div>
    `;
  }

  function where() {
    const acc = state.accounts.find((a) => a.id === state.account);
    const label = acc ? acc.address || acc.id : "none";
    $("where").innerHTML = `Account <b>${esc(label)}</b> · Mailbox <b>${esc(state.mailbox)}</b>`;
  }

  async function selectAccount(id) {
    state.account = id;
    state.current = null;
    renderAccounts();
    where();
    await loadMessages();
    renderRead();
  }

  async function selectMailbox(id) {
    state.mailbox = id;
    state.current = null;
    renderBoxes();
    where();
    await loadMessages();
    renderRead();
  }

  async function loadMessages() {
    if (!state.account) return;
    const q = new URLSearchParams({
      account: state.account,
      mailbox: state.mailbox,
      limit: "40",
    });
    if (state.unread) q.set("unread", "true");
    if (state.flagged || state.mailbox === "saved") q.set("flagged", "true");
    try {
      state.messages = (await api("GET", "/v1/messages?" + q.toString())) || [];
    } catch (err) {
      $("msgs").className = "error";
      $("msgs").textContent = err.message;
      return;
    }
    renderList();
  }

  async function openMessage(id) {
    try {
      state.current = await api("GET", `/v1/messages/${encodeURIComponent(id)}?account=${encodeURIComponent(state.account)}&mailbox=${encodeURIComponent(state.mailbox)}&body=1`);
    } catch (err) {
      toast(err.message);
      return;
    }
    renderList();
    renderRead();
  }

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  async function boot() {
    renderBoxes();
    try {
      const status = await api("GET", "/v1/health");
      $("engine").textContent = "Engine: connected";
      if (status && status.ts) $("engine").textContent = "Engine: live";
    } catch {
      $("engine").textContent = "Engine: not running — mailkit service start";
      return;
    }
    try {
      state.accounts = (await api("GET", "/v1/accounts")) || [];
    } catch (err) {
      toast(err.message);
      return;
    }
    renderAccounts();
    if (state.accounts[0]) await selectAccount(state.accounts[0].id);
  }

  function closeCompose() {
    state.replyId = null;
    $("compose").classList.remove("open");
    const title = $("compose-title");
    if (title) title.textContent = "Send";
  }

  $("compose-btn").addEventListener("click", () => {
    state.replyId = null;
    const title = $("compose-title");
    if (title) title.textContent = "Send";
    $("compose").classList.add("open");
  });
  $("compose-cancel").addEventListener("click", closeCompose);
  $("compose-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!state.account) {
      toast("Select an account first.");
      return;
    }
    try {
      if (state.replyId) {
        await api("POST", "/v1/reply", {
          account: state.account,
          id: state.replyId,
          to: [$("to").value],
          subject: $("subject").value,
          body: $("body").value,
        });
      } else {
        await api("POST", "/v1/send", {
          account: state.account,
          to: [$("to").value],
          subject: $("subject").value,
          body: $("body").value,
        });
      }
      closeCompose();
      toast("Sent.");
    } catch (err) {
      toast(err.message);
    }
  });
  $("doctor-btn").addEventListener("click", async () => {
    try {
      const report = await api("POST", "/v1/doctor/repair");
      const failed = (report && report.failed) || 0;
      toast(report && report.ok !== false && failed === 0 ? "Doctor: clear" : `${failed} issue(s) remain`);
    } catch (err) {
      toast(err.message);
    }
  });
  $("flag-btn").addEventListener("click", async () => {
    if (!state.current) return;
    const id = state.current.id;
    const action = state.current.flagged ? "unflag" : "flag";
    try {
      await api("POST", `/v1/messages/${encodeURIComponent(id)}/${action}`, {});
      toast(action === "flag" ? "Starred." : "Unstarred.");
      await loadMessages();
      await openMessage(id);
    } catch (err) {
      toast(err.message);
    }
  });
  $("archive-btn").addEventListener("click", async () => {
    if (!state.current) return;
    try {
      await api("POST", `/v1/messages/${state.current.id}/move`, { mailbox: "archives" });
      toast("Moved to Archive.");
      state.current = null;
      await loadMessages();
      renderRead();
    } catch (err) {
      toast(err.message);
    }
  });
  $("reply-btn").addEventListener("click", () => {
    if (!state.current) return;
    state.replyId = state.current.id;
    $("compose").classList.add("open");
    const title = $("compose-title");
    if (title) title.textContent = "Reply";
    $("subject").value = (state.current.subject || "").startsWith("Re:")
      ? state.current.subject
      : `Re: ${state.current.subject || ""}`;
    $("to").value = fromAddr(state.current);
  });
  document.querySelectorAll("[data-filter]").forEach((chip) => {
    chip.addEventListener("click", async () => {
      const key = chip.dataset.filter;
      state[key] = !state[key];
      chip.setAttribute("aria-pressed", String(state[key]));
      await loadMessages();
    });
  });

  window.mailkitDesktop = {
    setToken(token, base) {
      state.token = token || "";
      if (base) state.base = base;
      boot();
    },
  };

  boot();
})();
