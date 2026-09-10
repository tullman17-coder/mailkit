#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const APP = path.resolve(__dirname, "../desktop/js/app.js");
const HTML = path.resolve(__dirname, "../desktop/index.html");

function makeEl(id) {
  const listeners = {};
  return {
    id,
    disabled: false,
    textContent: "",
    className: "",
    innerHTML: "",
    dataset: {},
    attributes: {},
    classList: {
      add() {},
      remove() {},
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
    getAttribute(name) {
      return this.attributes[name];
    },
    addEventListener(type, fn) {
      listeners[type] = listeners[type] || [];
      listeners[type].push(fn);
    },
    async click() {
      for (const fn of listeners.click || []) await fn({ preventDefault() {} });
    },
    querySelectorAll() {
      return [];
    },
  };
}

async function runHarness() {
  const elements = {};
  const ids = [
    "toast",
    "accounts",
    "boxes",
    "msgs",
    "list-title",
    "flag-btn",
    "archive-btn",
    "reply-btn",
    "read",
    "where",
    "engine",
    "compose-btn",
    "compose-cancel",
    "compose-form",
    "compose",
    "to",
    "subject",
    "body",
    "doctor-btn",
  ];
  for (const id of ids) elements[id] = makeEl(id);
  elements["flag-btn"].textContent = "Star";
  elements["flag-btn"].setAttribute("aria-pressed", "false");

  const msgClicks = {};
  elements.msgs.querySelectorAll = (sel) => {
    if (sel !== ".msg") return [];
    const idsFound = [...String(elements.msgs.innerHTML).matchAll(/data-id="([^"]+)"/g)].map(
      (m) => m[1]
    );
    return idsFound.map((id) => ({
      dataset: { id },
      addEventListener(type, fn) {
        if (type === "click") msgClicks[id] = fn;
      },
    }));
  };

  const store = {
    m1: {
      id: "m1",
      subject: "Hello",
      account_id: "acc1",
      mailbox: "INBOX",
      from: [{ address: "ada@example.com" }],
      unread: true,
      flagged: false,
      body_text: "body",
      snippet: "body",
    },
  };
  const fetches = [];

  const context = {
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    encodeURIComponent,
    String,
    JSON,
    Error,
    window: {},
    document: {
      getElementById(id) {
        if (!elements[id]) elements[id] = makeEl(id);
        return elements[id];
      },
      querySelectorAll(sel) {
        if (sel === "[data-filter]") return [];
        return [];
      },
    },
    fetch: async (url, opts = {}) => {
      const method = opts.method || "GET";
      fetches.push({ method, url, body: opts.body });
      const u = String(url);
      let data;
      if (u.endsWith("/v1/health")) data = { ts: "now" };
      else if (u.endsWith("/v1/accounts")) {
        data = [{ id: "acc1", name: "Work", address: "ada@example.com", provider: "imap" }];
      } else if (u.includes("/v1/messages?") && method === "GET") {
        data = [store.m1].map(({ body_text, ...rest }) => rest);
      } else if (u.includes("/v1/messages/m1") && method === "GET") {
        data = { ...store.m1 };
      } else if (u.includes("/v1/messages/m1/flag") && method === "POST") {
        store.m1 = { ...store.m1, flagged: true };
        data = { id: "m1", action: "flag" };
      } else if (u.includes("/v1/messages/m1/unflag") && method === "POST") {
        store.m1 = { ...store.m1, flagged: false };
        data = { id: "m1", action: "unflag" };
      } else {
        throw new Error("unexpected fetch " + method + " " + u);
      }
      return {
        ok: true,
        statusText: "OK",
        json: async () => ({ ok: true, data }),
      };
    },
  };
  context.window = context.window;
  context.globalThis = context;

  vm.runInNewContext(fs.readFileSync(APP, "utf8"), context, { filename: APP });
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 20));

  assert.ok(msgClicks.m1, "message list should bind click handlers");
  await msgClicks.m1();
  await new Promise((r) => setTimeout(r, 20));

  assert.match(
    elements["flag-btn"].textContent + (elements["flag-btn"].attributes["aria-pressed"] || ""),
    /Star/i,
    "button should show unstarred state before toggle"
  );

  fetches.length = 0;
  await elements["flag-btn"].click();
  await new Promise((r) => setTimeout(r, 20));

  const afterStar = fetches.map((f) => `${f.method} ${f.url}`);
  assert.ok(
    afterStar.some((l) => l.includes("POST") && /\/v1\/messages\/m1\/flag\b/.test(l)),
    `expected POST /flag, got: ${afterStar.join(" | ")}`
  );
  assert.ok(
    afterStar.some((l) => l.includes("GET") && l.includes("/v1/messages?")),
    `expected list reload after star, got: ${afterStar.join(" | ")}`
  );
  assert.ok(
    afterStar.some((l) => l.includes("GET") && /\/v1\/messages\/m1\?/.test(l)),
    `expected reader reload after star, got: ${afterStar.join(" | ")}`
  );

  const listHtml = elements.msgs.innerHTML;
  assert.match(listHtml, /★|starred|data-flagged="true"/i, "list should mark flagged messages");
  assert.match(
    elements["flag-btn"].textContent + " " + (elements["flag-btn"].attributes["aria-pressed"] || ""),
    /Unstar|true/i,
    "button should reflect starred state"
  );

  fetches.length = 0;
  await elements["flag-btn"].click();
  await new Promise((r) => setTimeout(r, 20));
  const afterUnstar = fetches.map((f) => `${f.method} ${f.url}`);
  assert.ok(
    afterUnstar.some((l) => l.includes("POST") && /\/v1\/messages\/m1\/unflag\b/.test(l)),
    `expected POST /unflag on second click, got: ${afterUnstar.join(" | ")}`
  );
  assert.ok(
    afterUnstar.some((l) => l.includes("GET") && l.includes("/v1/messages?")),
    `expected list reload after unstar, got: ${afterUnstar.join(" | ")}`
  );
  assert.ok(
    afterUnstar.some((l) => l.includes("GET") && /\/v1\/messages\/m1\?/.test(l)),
    `expected reader reload after unstar, got: ${afterUnstar.join(" | ")}`
  );

  const html = fs.readFileSync(HTML, "utf8");
  assert.match(html, /id="flag-btn"[^>]*aria-pressed/, "star button should expose pressed state");

  const src = fs.readFileSync(APP, "utf8");
  assert.match(src, /flagged.*true/, "Saved filter flagged=true must remain (FLAG-F6)");
}

runHarness()
  .then(() => {
    console.log("test_desktop_star.js: ok");
  })
  .catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
  });
