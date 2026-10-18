#!/usr/bin/env node

// src/mcp/schema.ts
var Schema = class {
  /** @internal */
  optionalFlag = false;
  description;
  kind;
  pattern;
  minItems;
  minLen;
  itemSchema;
  constructor(kind, itemSchema) {
    this.kind = kind;
    this.itemSchema = itemSchema;
  }
  regex(re, message) {
    this.pattern = { re, message };
    return this;
  }
  /** minItems for arrays, minLength for strings. */
  min(n) {
    if (this.kind === "array") this.minItems = n;
    else this.minLen = n;
    return this;
  }
  describe(text) {
    this.description = text;
    return this;
  }
  optional() {
    this.optionalFlag = true;
    return this;
  }
  /** JSON Schema for tools/list. */
  toJsonSchema() {
    const base = {};
    if (this.description) base.description = this.description;
    switch (this.kind) {
      case "string":
        base.type = "string";
        if (this.pattern) base.pattern = this.pattern.re.source;
        if (this.minLen !== void 0) base.minLength = this.minLen;
        break;
      case "boolean":
        base.type = "boolean";
        break;
      case "array":
        base.type = "array";
        if (this.itemSchema) base.items = this.itemSchema.toJsonSchema();
        if (this.minItems !== void 0) base.minItems = this.minItems;
        break;
      case "record":
        base.type = "object";
        base.additionalProperties = true;
        break;
      case "unknown":
        break;
    }
    return base;
  }
  /** Validate a value; returns an error message or null. */
  validate(value, path3) {
    switch (this.kind) {
      case "unknown":
      case "record":
        return this.kind === "record" && (typeof value !== "object" || value === null || Array.isArray(value)) ? `${path3} must be an object` : null;
      case "string":
        if (typeof value !== "string") return `${path3} must be a string`;
        if (this.minLen !== void 0 && value.length < this.minLen)
          return `${path3} must be at least ${this.minLen} characters`;
        if (this.pattern && !this.pattern.re.test(value)) return `${path3}: ${this.pattern.message}`;
        return null;
      case "boolean":
        return typeof value === "boolean" ? null : `${path3} must be a boolean`;
      case "array": {
        if (!Array.isArray(value)) return `${path3} must be an array`;
        if (this.minItems !== void 0 && value.length < this.minItems)
          return `${path3} must have at least ${this.minItems} item(s)`;
        if (this.itemSchema) {
          for (let i = 0; i < value.length; i += 1) {
            const err = this.itemSchema.validate(value[i], `${path3}[${i}]`);
            if (err) return err;
          }
        }
        return null;
      }
    }
  }
};
var z = {
  string: () => new Schema("string"),
  boolean: () => new Schema("boolean"),
  unknown: () => new Schema("unknown"),
  array: (item) => new Schema("array", item),
  // record(keyType, valueType) — only the object-ness is enforced.
  record: (_key, _value) => new Schema("record")
};
function inputJsonSchema(fields) {
  const properties = {};
  const required = [];
  for (const [name, schema] of Object.entries(fields)) {
    properties[name] = schema.toJsonSchema();
    if (!schema.optionalFlag) required.push(name);
  }
  const out = { type: "object", properties };
  if (required.length) out.required = required;
  return out;
}
function validateArgs(fields, args) {
  const obj = args ?? {};
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
    return { ok: false, error: "arguments must be an object" };
  }
  for (const [name, schema] of Object.entries(fields)) {
    const present = obj[name] !== void 0;
    if (!present) {
      if (!schema.optionalFlag) return { ok: false, error: `missing required argument "${name}"` };
      continue;
    }
    const err = schema.validate(obj[name], name);
    if (err) return { ok: false, error: err };
  }
  return { ok: true };
}

// src/mcp/server.ts
var PROTOCOL_VERSION = "2025-06-18";
var METHOD_NOT_FOUND = -32601;
var INVALID_PARAMS = -32602;
var INTERNAL_ERROR = -32603;
var StdioServerTransport = class {
  buffer = "";
  onLine = null;
  start(onMessage) {
    this.onLine = onMessage;
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => this.feed(chunk));
  }
  feed(chunk) {
    this.buffer += chunk;
    let index = this.buffer.indexOf("\n");
    while (index !== -1) {
      const line = this.buffer.slice(0, index).trim();
      this.buffer = this.buffer.slice(index + 1);
      if (line) {
        try {
          this.onLine?.(JSON.parse(line));
        } catch {
        }
      }
      index = this.buffer.indexOf("\n");
    }
  }
  send(message) {
    process.stdout.write(`${JSON.stringify(message)}
`);
  }
};
var McpServer = class {
  constructor(info, options = {}) {
    this.info = info;
    this.options = options;
  }
  info;
  options;
  tools = /* @__PURE__ */ new Map();
  transport = null;
  registerTool(name, def, handler) {
    this.tools.set(name, { name, ...def, handler });
  }
  async connect(transport) {
    this.transport = transport;
    transport.start((msg) => void this.handle(msg));
  }
  async handle(msg) {
    const isRequest = msg.id !== void 0 && msg.id !== null;
    const reply = (result) => this.transport?.send({ jsonrpc: "2.0", id: msg.id ?? null, result });
    const fail = (code, message) => this.transport?.send({ jsonrpc: "2.0", id: msg.id ?? null, error: { code, message } });
    try {
      switch (msg.method) {
        case "initialize": {
          const clientVersion = msg.params?.protocolVersion ?? PROTOCOL_VERSION;
          reply({
            protocolVersion: clientVersion,
            capabilities: { tools: {} },
            serverInfo: this.info,
            ...this.options.instructions ? { instructions: this.options.instructions } : {}
          });
          return;
        }
        case "notifications/initialized":
        case "initialized":
          return;
        // notification, nothing to answer
        case "ping":
          if (isRequest) reply({});
          return;
        case "tools/list": {
          const tools = [...this.tools.values()].map((t) => ({
            name: t.name,
            ...t.title ? { title: t.title } : {},
            description: t.description,
            inputSchema: inputJsonSchema(t.inputSchema)
          }));
          reply({ tools });
          return;
        }
        case "tools/call": {
          const name = msg.params?.name;
          const args = msg.params?.arguments ?? {};
          const tool = this.tools.get(name);
          if (!tool) {
            fail(INVALID_PARAMS, `Unknown tool: ${name}`);
            return;
          }
          const check = validateArgs(tool.inputSchema, args);
          if (!check.ok) {
            fail(INVALID_PARAMS, check.error ?? "invalid arguments");
            return;
          }
          const result = await tool.handler(args);
          reply(result);
          return;
        }
        default:
          if (isRequest) fail(METHOD_NOT_FOUND, `Method not found: ${msg.method}`);
          return;
      }
    } catch (err) {
      if (isRequest) fail(INTERNAL_ERROR, err.message);
    }
  }
};

// src/config.ts
var ConfigError = class extends Error {
};
function flag(name, fallback = false) {
  const raw = process.env[name];
  if (raw === void 0 || raw === "") return fallback;
  return ["1", "true", "yes", "on"].includes(raw.trim().toLowerCase());
}
function loadConfig() {
  const host = process.env.DECO_HOST?.trim();
  const password = process.env.DECO_PASSWORD;
  if (!host) {
    throw new ConfigError(
      "DECO_HOST is not set. Point it at your Deco, e.g. DECO_HOST=192.168.68.1"
    );
  }
  if (!password) {
    throw new ConfigError(
      "DECO_PASSWORD is not set. Put your Deco admin password in .env (see .env.example) \u2014 never pass it on the command line."
    );
  }
  const timeoutRaw = process.env.DECO_TIMEOUT_MS?.trim();
  const timeoutMs = timeoutRaw ? Number(timeoutRaw) : 15e3;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new ConfigError(`DECO_TIMEOUT_MS must be a positive number, got "${timeoutRaw}"`);
  }
  return {
    host,
    // The Deco web UI has no real username; the firmware hashes it in anyway.
    username: process.env.DECO_USERNAME ?? "admin",
    password,
    allowWrite: flag("DECO_ALLOW_WRITE"),
    allowDangerous: flag("DECO_ALLOW_DANGEROUS"),
    revealSecrets: flag("DECO_REVEAL_SECRETS"),
    timeoutMs,
    // The router's certificate is self-signed, so verification is off unless asked.
    rejectUnauthorized: flag("DECO_VERIFY_TLS"),
    stateDir: process.env.DECO_STATE_DIR?.trim() || void 0
  };
}

// src/deco/crypto.ts
import crypto from "node:crypto";
var PKCS1_HEADER_BYTES = 11;
var AES_DIGITS = 16;
function normalizeHex(hex) {
  const stripped = hex.replace(/^(00)+/, "");
  const even = stripped.length % 2 ? `0${stripped}` : stripped;
  return even.length === 0 ? "00" : even;
}
function publicKeyFromHex(nHex, eHex) {
  const toB64Url = (hex) => Buffer.from(normalizeHex(hex), "hex").toString("base64url");
  return crypto.createPublicKey({
    key: { kty: "RSA", n: toB64Url(nHex), e: toB64Url(eHex) },
    format: "jwk"
  });
}
function rsaEncrypt(key, plaintext) {
  const modulusBytes = (key.asymmetricKeyDetails?.modulusLength ?? 0) / 8;
  if (!modulusBytes) throw new Error("RSA key has no usable modulus length");
  const chunkSize = modulusBytes - PKCS1_HEADER_BYTES;
  const parts = [];
  for (let i = 0; i < plaintext.length; i += chunkSize) {
    const block = plaintext.subarray(i, i + chunkSize);
    parts.push(
      crypto.publicEncrypt({ key, padding: crypto.constants.RSA_PKCS1_PADDING }, block).toString("hex")
    );
  }
  return parts.join("");
}
function aesEncrypt(key, iv, plaintext) {
  const cipher = crypto.createCipheriv("aes-128-cbc", key, iv);
  return Buffer.concat([cipher.update(plaintext), cipher.final()]).toString("base64");
}
function aesDecrypt(key, iv, ciphertextB64) {
  const decipher = crypto.createDecipheriv("aes-128-cbc", key, iv);
  const raw = Buffer.from(ciphertextB64, "base64");
  return Buffer.concat([decipher.update(raw), decipher.final()]).toString("utf8");
}
function randomAesDigits() {
  const min = 10n ** BigInt(AES_DIGITS - 1);
  const span = 10n ** BigInt(AES_DIGITS) - min;
  const draw = BigInt(`0x${crypto.randomBytes(8).toString("hex")}`);
  return (min + draw % span).toString();
}
function sessionHash(username, password) {
  return crypto.createHash("md5").update(`${username}${password}`).digest("hex");
}
function buildSignText(aesKey, aesIv, hash, seq, dataLen) {
  return `k=${aesKey}&i=${aesIv}&h=${hash}&s=${seq + dataLen}`;
}

// src/deco/http.ts
import http from "node:http";
import https from "node:https";

// src/deco/errors.ts
var DecoError = class extends Error {
  constructor(message) {
    super(message);
    this.name = this.constructor.name;
  }
};
var DecoLoginError = class extends DecoError {
  constructor(message, attemptsAllowed) {
    super(message);
    this.attemptsAllowed = attemptsAllowed;
  }
  attemptsAllowed;
};
var DecoAuthExpiredError = class extends DecoError {
  constructor(message, clean = false) {
    super(message);
    this.clean = clean;
  }
  clean;
};
var DecoApiError = class extends DecoError {
  constructor(message, errorCode) {
    super(message);
    this.errorCode = errorCode;
  }
  errorCode;
};
var DecoWriteBlockedError = class extends DecoError {
};
var DecoTimeoutError = class extends DecoError {
};

// src/deco/http.ts
async function post(url, body, opts) {
  const maxRedirects = opts.maxRedirects ?? 3;
  let current = url;
  for (let hop = 0; ; hop++) {
    const result = await postOnce(current, body, opts);
    const location = result.redirectTo;
    if (!location || hop >= maxRedirects) {
      return {
        status: result.status,
        setCookie: result.setCookie,
        body: result.body,
        finalUrl: current
      };
    }
    current = new URL(location, current).toString();
  }
}
function postOnce(url, body, opts) {
  const target = new URL(url);
  const transport = target.protocol === "https:" ? https : http;
  return new Promise((resolve2, reject) => {
    const req = transport.request(
      target,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body).toString(),
          ...opts.headers
        },
        // The Deco ships a self-signed certificate; scoped to this request only.
        ...target.protocol === "https:" ? { rejectUnauthorized: opts.rejectUnauthorized } : {}
      },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const status = res.statusCode ?? 0;
          const location = res.headers.location;
          resolve2({
            status,
            setCookie: res.headers["set-cookie"] ?? [],
            body: Buffer.concat(chunks).toString("utf8"),
            redirectTo: status >= 300 && status < 400 && location ? location : void 0
          });
        });
      }
    );
    req.setTimeout(opts.timeoutMs, () => {
      req.destroy(new DecoTimeoutError(`Request to ${target.host} timed out`));
    });
    req.on("error", reject);
    req.end(body);
  });
}

// src/deco/catalog.ts
var CATALOG = [
  // --- mesh nodes ---
  {
    endpoint: "admin/device",
    form: "device_list",
    operations: { read: "read" },
    summary: "The Deco units in the mesh, with role, signal and firmware."
  },
  {
    endpoint: "admin/device",
    form: "system",
    operations: { reboot: "dangerous" },
    summary: "Reboot one or more Deco units."
  },
  {
    endpoint: "admin/device",
    form: "speedtest",
    operations: { read: "read", write: "write", stop: "write" },
    summary: "WAN speed test: read the last result, start a run, or stop one."
  },
  // --- clients ---
  {
    endpoint: "admin/client",
    form: "client_list",
    operations: { read: "read" },
    summary: "Connected clients, per node or across the whole mesh."
  },
  {
    endpoint: "admin/client",
    form: "traffic_stat",
    // The wire operation is "list", not "read" - "read" is rejected.
    operations: { list: "read" },
    summary: "Per-client traffic counters."
  },
  {
    endpoint: "admin/client",
    form: "black_list",
    // Enumeration is "list" on the wire. add/remove are documented but this
    // firmware rejects them for a MAC it does not already know as a client.
    operations: { list: "read", add: "write", remove: "write" },
    summary: "Blocked clients."
  },
  {
    endpoint: "admin/client",
    form: "addr_reservation",
    operations: {
      getlist: "read",
      add: "write",
      modify: "write",
      remove: "write"
    },
    summary: "Static DHCP address reservations."
  },
  {
    endpoint: "admin/client",
    form: "client",
    operations: { read: "read", write: "write" },
    summary: "Details and nickname of a single client."
  },
  {
    endpoint: "admin/client",
    form: "client_access",
    operations: { read: "read" },
    summary: "Per-client signal bars (signal_level_2g/5g, 0-3) and band. The only per-client signal M9 Plus 1.9.1 exposes; hostnames read UNKNOWN, so join names from client_list."
  },
  // --- network ---
  {
    endpoint: "admin/network",
    form: "performance",
    operations: { read: "read" },
    summary: "CPU and memory load of the mesh nodes."
  },
  {
    endpoint: "admin/network",
    form: "internet",
    operations: { read: "read" },
    summary: "Internet connectivity status."
  },
  {
    endpoint: "admin/network",
    form: "wan_ipv4",
    operations: { read: "read" },
    summary: "IPv4 WAN configuration and link state."
  },
  {
    endpoint: "admin/network",
    form: "lan_ipv4",
    operations: { read: "read" },
    summary: "LAN address and subnet mask of the gateway node."
  },
  {
    endpoint: "admin/network",
    form: "dhcp_dial",
    operations: { read: "read" },
    summary: "WAN-side DHCP client mode (whether the WAN IP is leased)."
  },
  {
    endpoint: "admin/network",
    form: "ipv6",
    operations: { read: "read" },
    summary: "IPv6 configuration. Not used standalone on M9 Plus 1.9.1 - IPv6 status is included in the internet form instead."
  },
  // --- wireless ---
  {
    endpoint: "admin/wireless",
    form: "wlan",
    operations: { read: "read", write: "dangerous" },
    summary: "SSIDs, bands and security. Writing one drops every client."
  },
  {
    endpoint: "admin/wireless",
    form: "bridge",
    operations: { read: "read" },
    summary: "Node placement grade and PLC support (location: good/fair/poor)."
  },
  {
    endpoint: "admin/wireless",
    form: "bandwidth_enhance",
    operations: { read: "read" },
    summary: "160 MHz channel width (HT160) enable flag."
  },
  {
    endpoint: "admin/wireless",
    form: "beamforming",
    // Write unverified (toggling a radio could disrupt Wi-Fi); read-only until
    // confirmed. deco_capabilities shows it served for read on this firmware.
    operations: { read: "read" },
    summary: "Beamforming on/off (read-only here; write unverified)."
  },
  {
    endpoint: "admin/wireless",
    form: "ieee80211r",
    // Write unverified; read-only until confirmed.
    operations: { read: "read" },
    summary: "802.11r fast roaming on/off (read-only here; write unverified)."
  },
  {
    endpoint: "admin/wireless",
    form: "operation_mode",
    operations: { read: "read" },
    summary: "Radio operation mode (AP/router role of the radios)."
  },
  {
    endpoint: "admin/network",
    form: "vlan",
    operations: { read: "read" },
    summary: "VLAN enable flag."
  },
  {
    endpoint: "admin/network",
    form: "igmp_setting",
    operations: { read: "read" },
    summary: "IGMP snooping (multicast/IPTV) setting."
  },
  {
    endpoint: "admin/network",
    form: "mac_clone",
    operations: { read: "read" },
    summary: "WAN MAC clone enable flag."
  },
  {
    endpoint: "admin/wireless",
    form: "power",
    operations: { read: "read", write: "write" },
    // On M9 Plus 1.9.1 the read returns {support_dfs}, not a power level.
    summary: "DFS support flag (this firmware returns support_dfs, not a TX power level)."
  },
  {
    endpoint: "admin/wireless",
    form: "wifi_schedule",
    operations: { read: "read", write: "write" },
    // Not served on M9 Plus 1.9.1 (read returns error_code 1) - app-only here.
    // Kept for Deco models that serve the app form set; deco_capabilities shows
    // which unit serves it.
    summary: "Scheduled Wi-Fi on/off windows (app-only on M9 Plus 1.9.1)."
  },
  // --- dhcp ---
  // --- system ---
  {
    endpoint: "admin/device",
    form: "mode",
    operations: { read: "read" },
    summary: "Work mode and system mode (Router or Access Point)."
  },
  {
    endpoint: "admin/network",
    form: "wan_mode",
    operations: { read: "read" },
    summary: "WAN connection mode."
  },
  {
    endpoint: "admin/device",
    form: "timesetting",
    operations: { read: "read" },
    summary: "Timezone and NTP configuration."
  },
  // --- ui / component flags ---
  {
    endpoint: "admin/component_control",
    form: "switch_list",
    operations: { read: "read" },
    summary: "Web-UI component flags. On M9 Plus 1.9.1 reports show_performance - whether the performance/insights page is exposed in the UI."
  }
];
function resolve(endpoint, form, operation) {
  const entry = CATALOG.find((e) => e.endpoint === endpoint && e.form === form);
  if (!entry) {
    return {
      error: `${endpoint}?form=${form} is not in the catalogue. Reachable forms: ${CATALOG.map((e) => `${e.endpoint}?form=${e.form}`).join(", ")}`
    };
  }
  const risk = entry.operations[operation];
  if (!risk) {
    return {
      error: `Operation "${operation}" is not defined for ${endpoint}?form=${form}. Available: ${Object.keys(entry.operations).join(", ")}`
    };
  }
  return { ...entry, operation, risk };
}
var READ_ONLY_OPERATIONS = /* @__PURE__ */ new Set(["read", "list", "getlist", "get"]);
function isMutatingOperation(op) {
  return !READ_ONLY_OPERATIONS.has(op.toLowerCase());
}
function gate(risk, perms) {
  if (risk === "read") return { allowed: true };
  if (!perms.allowWrite) return { allowed: false, reason: "read-only" };
  if (risk === "dangerous" && !perms.allowDangerous) {
    return { allowed: false, reason: "dangerous-blocked" };
  }
  return { allowed: true };
}

// src/deco/client.ts
var DecoClient = class {
  constructor(opts) {
    this.opts = opts;
    this.baseUrl = normalizeHost(opts.host);
    this.timeoutMs = opts.timeoutMs ?? 15e3;
    this.rejectUnauthorized = opts.rejectUnauthorized ?? false;
    this.timeoutRetries = opts.timeoutRetries ?? 2;
  }
  opts;
  session = null;
  loginInFlight = null;
  /**
   * Serialises every dispatch. The firmware holds one session at a time and the
   * retry paths null out `this.session` mid-flight, so two concurrent calls
   * sharing one session corrupt each other. This promise chain is the mutex:
   * each acquirer waits on the previous one settling before it runs.
   */
  mutex = Promise.resolve();
  baseUrl;
  timeoutMs;
  rejectUnauthorized;
  timeoutRetries;
  get host() {
    return this.baseUrl;
  }
  /** True once a session token is held. Does not prove the token is still valid. */
  get isAuthenticated() {
    return this.session !== null;
  }
  /**
   * Run an authenticated call, logging in on demand. Serialised against every
   * other call and transaction on this client. A stale session is retried
   * exactly once with fresh credentials before the error propagates.
   */
  async call(request) {
    return this.lock(() => this.dispatchWithRetry(request));
  }
  /**
   * Run a sequence of dispatches atomically with respect to other callers: the
   * mutex is acquired once for the whole block. `fn` receives a `run` that
   * dispatches WITHOUT re-acquiring the lock — calling the public `call()` from
   * inside would deadlock. Use this for composites like getlist→remove→add that
   * must not interleave with another operation touching the same session.
   */
  async transaction(fn) {
    return this.lock(() => fn((request) => this.dispatchWithRetry(request)));
  }
  /** Serialise `fn` behind every earlier acquirer of the mutex. */
  lock(fn) {
    const run = this.mutex.then(fn, fn);
    this.mutex = run.then(
      () => void 0,
      () => void 0
    );
    return run;
  }
  /**
   * The retry loop, run while already holding the mutex. Reads are retried on a
   * stale session and on transient network failures. Mutating operations are
   * treated conservatively: a write whose response was lost may already have
   * been applied server-side, so it is retried ONLY on a clean 401/403 auth
   * rejection (which proves the firmware refused it before applying), never on
   * an ambiguous empty payload and never on a transient failure.
   */
  async dispatchWithRetry(request) {
    const mutating = isMutatingOperation(request.operation);
    let staleRetried = false;
    let timeoutsSeen = 0;
    for (; ; ) {
      try {
        await this.ensureLogin();
        return await this.dispatch(request);
      } catch (err) {
        if (err instanceof DecoAuthExpiredError && !staleRetried && (!mutating || err.clean)) {
          staleRetried = true;
          this.session = null;
          continue;
        }
        if (!mutating && isTransient(err) && timeoutsSeen < this.timeoutRetries) {
          timeoutsSeen += 1;
          this.session = null;
          await delay(500 * timeoutsSeen);
          continue;
        }
        throw err;
      }
    }
  }
  /** Drop the local session. The firmware keeps its own until it times out. */
  async logout() {
    if (!this.session) return;
    try {
      await this.dispatch({
        endpoint: "admin/system",
        form: "logout",
        operation: "logout"
      });
    } catch {
    } finally {
      this.session = null;
    }
  }
  // --- session handling -------------------------------------------------
  async ensureLogin() {
    if (this.session) return;
    if (this.loginInFlight) return this.loginInFlight;
    this.loginInFlight = this.login().finally(() => {
      this.loginInFlight = null;
    });
    return this.loginInFlight;
  }
  async login() {
    const aesKey = randomAesDigits();
    const aesIv = randomAesDigits();
    const passwordKey = await this.fetchPasswordKey();
    const { signKey, seq } = await this.fetchSignKey();
    const pending = {
      aesKey,
      aesIv,
      aesKeyBytes: Buffer.from(aesKey, "utf8"),
      aesIvBytes: Buffer.from(aesIv, "utf8"),
      passwordKey,
      signKey,
      seq,
      stok: "",
      cookie: ""
    };
    const encryptedPassword = rsaEncrypt(
      passwordKey,
      Buffer.from(this.opts.password, "utf8")
    );
    const body = this.encodeEnvelope(pending, {
      operation: "login",
      params: { password: encryptedPassword }
    });
    const res = await post(`${this.baseUrl}/cgi-bin/luci/;stok=/login?form=login`, body, {
      timeoutMs: this.timeoutMs,
      rejectUnauthorized: this.rejectUnauthorized
    });
    this.pinSchemeFrom(res.finalUrl);
    if (res.status === 403) {
      throw new DecoLoginError(
        "Login refused (HTTP 403). The Deco allows one admin session at a time \u2014 log out of the Deco app or the web UI, or use a dedicated manager account."
      );
    }
    const outer = parseJson(res.body, "login response");
    const payload = decodeEnvelope(pending, outer.data);
    const errorCode = Number(payload.error_code ?? 0);
    if (errorCode !== 0) {
      if (errorCode === -5002) {
        const attempts = payload.result?.attemptsAllowed;
        throw new DecoLoginError(
          `Wrong username or password (error_code ${errorCode}).`,
          attempts
        );
      }
      throw new DecoApiError(`Login failed with error_code ${errorCode}`, errorCode);
    }
    const stok = payload.result?.stok;
    if (typeof stok !== "string" || !stok) {
      throw new DecoError("Login succeeded but no stok token was returned");
    }
    const cookie = extractSysauth(res.setCookie);
    if (!cookie) {
      throw new DecoError("Login succeeded but no sysauth cookie was returned");
    }
    pending.stok = stok;
    pending.cookie = cookie;
    this.session = pending;
  }
  async fetchPasswordKey() {
    const json = await this.plainCall("keys");
    const keys = json.result?.password;
    if (!Array.isArray(keys) || keys.length < 2) {
      throw new DecoError("Unexpected response from ?form=keys");
    }
    return publicKeyFromHex(String(keys[0]), String(keys[1]));
  }
  async fetchSignKey() {
    const json = await this.plainCall("auth");
    const result = json.result;
    const key = result?.key;
    if (!Array.isArray(key) || key.length < 2 || typeof result?.seq !== "number") {
      throw new DecoError("Unexpected response from ?form=auth");
    }
    return {
      signKey: publicKeyFromHex(String(key[0]), String(key[1])),
      seq: result.seq
    };
  }
  /** The two handshake endpoints take plain JSON and need no session. */
  async plainCall(form) {
    const res = await post(
      `${this.baseUrl}/cgi-bin/luci/;stok=/login?form=${form}`,
      JSON.stringify({ operation: "read" }),
      { timeoutMs: this.timeoutMs, rejectUnauthorized: this.rejectUnauthorized }
    );
    this.pinSchemeFrom(res.finalUrl);
    return parseJson(res.body, `?form=${form} response`);
  }
  // --- request envelope -------------------------------------------------
  async dispatch(request) {
    const session = this.session;
    if (!session) throw new DecoAuthExpiredError("No active session", true);
    const body = this.encodeEnvelope(session, {
      operation: request.operation,
      ...request.params ? { params: request.params } : {}
    });
    const url = `${this.baseUrl}/cgi-bin/luci/;stok=${session.stok}/${request.endpoint}?form=${encodeURIComponent(request.form)}`;
    const res = await post(url, body, {
      timeoutMs: this.timeoutMs,
      rejectUnauthorized: this.rejectUnauthorized,
      headers: { Cookie: session.cookie }
    });
    this.pinSchemeFrom(res.finalUrl);
    if (res.status === 401 || res.status === 403) {
      throw new DecoAuthExpiredError(`Session rejected with HTTP ${res.status}`, true);
    }
    const refreshed = extractSysauth(res.setCookie);
    if (refreshed) session.cookie = refreshed;
    const outer = parseOuterBody(res.body, `${request.endpoint}?form=${request.form}`);
    if (!outer.data) {
      const outerCode = outer.error_code;
      if (outerCode !== void 0 && Number(outerCode) !== 0) {
        throw new DecoApiError(
          `${request.endpoint}?form=${request.form} returned error_code ${outerCode}`,
          outerCode
        );
      }
      throw new DecoAuthExpiredError(
        `${request.endpoint}?form=${request.form} returned an empty payload`
      );
    }
    const payload = decodeEnvelope(session, outer.data);
    const code = payload.error_code ?? payload.errorcode;
    if (code !== void 0 && Number(code) !== 0) {
      throw new DecoApiError(
        `${request.endpoint}?form=${request.form} returned error_code ${code}`,
        code
      );
    }
    return payload.result ?? payload;
  }
  /**
   * Build the wire body: AES-encrypted JSON in `data`, and a `sign` blob that
   * binds the AES key, the credential hash and `seq + len(data)`.
   */
  encodeEnvelope(session, payload) {
    const data = aesEncrypt(
      session.aesKeyBytes,
      session.aesIvBytes,
      Buffer.from(JSON.stringify(payload), "utf8")
    );
    const hash = sessionHash(this.opts.username, this.opts.password);
    const signText = buildSignText(
      session.aesKey,
      session.aesIv,
      hash,
      session.seq,
      data.length
    );
    const sign = rsaEncrypt(session.signKey, Buffer.from(signText, "utf8"));
    return `sign=${sign}&data=${encodeURIComponent(data)}`;
  }
  /**
   * Some firmwares 307-redirect every http:// call to https://. Once we have
   * followed one, keep using the scheme that worked.
   */
  pinSchemeFrom(finalUrl) {
    try {
      const scheme = new URL(finalUrl).protocol;
      if (scheme === "https:" && this.baseUrl.startsWith("http://")) {
        this.baseUrl = `https://${this.baseUrl.slice("http://".length)}`;
      }
    } catch {
    }
  }
};
function decodeEnvelope(session, data) {
  if (typeof data !== "string" || !data) {
    throw new DecoAuthExpiredError("Response payload was empty");
  }
  const plain = aesDecrypt(session.aesKeyBytes, session.aesIvBytes, data);
  const trimmed = plain.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) {
    throw new DecoError(`Firmware replied: ${trimmed.slice(0, 200)}`);
  }
  return parseJson(trimmed, "decrypted payload");
}
function isTransient(err) {
  if (err instanceof DecoTimeoutError) return true;
  const code = err?.code;
  return code === "ETIMEDOUT" || code === "ECONNRESET" || code === "ENETUNREACH" || code === "EHOSTUNREACH" || code === "ECONNREFUSED" || code === "EPIPE";
}
function delay(ms) {
  return new Promise((resolve2) => setTimeout(resolve2, ms));
}
function parseOuterBody(text, context) {
  const trimmed = text.trim();
  if (trimmed.startsWith("{")) return parseJson(trimmed, context);
  if (/^[A-Za-z0-9+/]+={0,2}$/.test(trimmed) && trimmed.length % 4 === 0) {
    return { data: trimmed };
  }
  return parseJson(trimmed, context);
}
function parseJson(text, context) {
  try {
    return JSON.parse(text);
  } catch {
    const preview = text.slice(0, 200);
    throw new DecoError(`Could not parse ${context} as JSON: ${preview}`);
  }
}
function extractSysauth(setCookie) {
  for (const header of setCookie) {
    const match = /(sysauth=[A-Za-z0-9]+)/.exec(header);
    if (match?.[1]) return match[1];
  }
  return null;
}
function normalizeHost(host) {
  const withScheme = /^https?:\/\//i.test(host) ? host : `http://${host}`;
  return withScheme.replace(/\/+$/, "");
}

// src/deco/detect.ts
async function detectDevice(client) {
  const result = await client.call({
    endpoint: "admin/device",
    form: "device_list",
    operation: "read"
  });
  const nodes = result.device_list ?? [];
  const master = nodes.find((n) => String(n.role ?? "").toLowerCase() === "master") ?? nodes[0];
  if (!master) return null;
  return {
    model: String(master.device_model ?? master.hardware_ver ?? "unknown"),
    firmware: String(master.software_ver ?? "unknown"),
    hardware: master.hardware_ver ? String(master.hardware_ver) : void 0,
    role: String(master.role ?? "unknown")
  };
}
async function probeCapabilities(client) {
  const device = await detectDevice(client).catch(() => null);
  const served = [];
  const unserved = [];
  const errors = {};
  for (const entry of CATALOG) {
    const readOp = Object.entries(entry.operations).find(([, risk]) => risk === "read");
    if (!readOp) continue;
    const key = `${entry.endpoint}?form=${entry.form}`;
    try {
      await client.call({
        endpoint: entry.endpoint,
        form: entry.form,
        operation: readOp[0],
        // A couple of forms want a selector; "default" is accepted where it applies.
        ...entry.form === "client_list" ? { params: { device_mac: "default" } } : {}
      });
      served.push(key);
    } catch (err) {
      unserved.push(key);
      if (!(err instanceof DecoApiError)) {
        errors[key] = err.message.slice(0, 120);
      }
    }
  }
  return { device, served, unserved, errors };
}

// src/state.ts
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
function defaultStateDir() {
  if (process.env.DECO_STATE_DIR) return process.env.DECO_STATE_DIR;
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(here, "..", ".deco-state");
}
var STATE_DIR = defaultStateDir();
function ensureDir() {
  fs.mkdirSync(STATE_DIR, { recursive: true });
}
function readState(name) {
  const file = path.join(STATE_DIR, `${name}.json`);
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}
function writeState(name, value) {
  ensureDir();
  const file = path.join(STATE_DIR, `${name}.json`);
  fs.writeFileSync(file, JSON.stringify(value, null, 2), "utf8");
  return file;
}

// src/deco/format.ts
var LEGACY_DECODE_ERROR = /^<Error Decoding (.*)>$/;
function hasControlChars(text) {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code < 32 && code !== 9 && code !== 10 && code !== 13) return true;
  }
  return false;
}
function canonicalMac(s) {
  return s.toUpperCase().replace(/:/g, "-");
}
function decodeName(value) {
  if (typeof value !== "string" || value === "") return value;
  const unwrap = (s) => {
    const match = LEGACY_DECODE_ERROR.exec(s);
    return match ? match[1] : s;
  };
  try {
    const decoded = Buffer.from(value, "base64");
    if (decoded.toString("base64").replace(/=+$/, "") !== value.replace(/=+$/, "")) {
      return unwrap(value);
    }
    const text = decoded.toString("utf8");
    if (hasControlChars(text) || text.includes("\uFFFD")) return unwrap(value);
    return unwrap(text);
  } catch {
    return unwrap(value);
  }
}
function isSecretKey(key) {
  const k = key.toLowerCase();
  return /pass|pwd|psk|pmk|passphrase|secret|sae/.test(k) || /(^|_)keys?(_|$)/.test(k) || k === "sysauth";
}
var NAME_KEYS = /* @__PURE__ */ new Set(["name", "custom_nickname", "nickname", "ssid", "client_name"]);
function normalize(value, revealSecrets) {
  if (Array.isArray(value)) {
    return value.map((item) => normalize(item, revealSecrets));
  }
  if (value === null || typeof value !== "object") return value;
  const out = {};
  for (const [key, raw] of Object.entries(value)) {
    const lower = key.toLowerCase();
    const isSet = raw !== "" && raw !== null && raw !== void 0;
    if (!revealSecrets && isSecretKey(key) && isSet) {
      out[key] = "[redacted - set DECO_REVEAL_SECRETS=1 to include]";
      continue;
    }
    if (NAME_KEYS.has(lower)) {
      out[key] = decodeName(raw);
      continue;
    }
    out[key] = normalize(raw, revealSecrets);
  }
  return out;
}
function humanRate(kilobytesPerSecond) {
  if (typeof kilobytesPerSecond !== "number" || !Number.isFinite(kilobytesPerSecond)) {
    return void 0;
  }
  const units = ["Kbps", "Mbps", "Gbps"];
  let value = kilobytesPerSecond * 8;
  let unit = 0;
  while (value >= 1e3 && unit < units.length - 1) {
    value /= 1e3;
    unit += 1;
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
}

// src/deco/analyze.ts
function nodeName(n) {
  const nick = decodeName(n.custom_nickname ?? n.nickname);
  return typeof nick === "string" && nick ? nick : String(n.device_model ?? "Deco");
}
function uplinkLabel(n) {
  if (n.connection_type === void 0) return "gateway (WAN)";
  if (Array.isArray(n.connection_type) && n.connection_type.includes("wired")) {
    return "wired backhaul";
  }
  return "wireless backhaul";
}
function buildTopology(nodes) {
  const macOf = (n) => String(n.mac ?? "").toUpperCase();
  const byMac = /* @__PURE__ */ new Map();
  for (const n of nodes) byMac.set(macOf(n), n);
  const childrenOf = (parentMac) => nodes.filter((n) => String(n.previous ?? "").toUpperCase() === parentMac);
  const visited = /* @__PURE__ */ new Set();
  const build = (n) => {
    visited.add(n);
    const mac = macOf(n);
    const kids = childrenOf(mac).filter((c) => c !== n && !visited.has(c));
    return {
      name: nodeName(n),
      mac,
      role: n.role,
      ip: n.device_ip,
      uplink: uplinkLabel(n),
      children: kids.map(build)
    };
  };
  const isRoot = (n) => {
    const prev = String(n.previous ?? "").toUpperCase();
    return !prev || !byMac.has(prev);
  };
  const trees = [];
  for (const n of nodes.filter(isRoot)) {
    if (!visited.has(n)) trees.push(build(n));
  }
  for (const n of nodes) {
    if (!visited.has(n)) trees.push(build(n));
  }
  return trees;
}
function weakestWireless(signals) {
  const levels = Object.entries(signals).map(([band, v]) => {
    const level = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v)) ? Number(v) : NaN;
    return { band, level };
  }).filter((l) => Number.isFinite(l.level));
  return levels.reduce(
    (min, cur) => !min || cur.level < min.level ? cur : min,
    void 0
  );
}
function nodeHealth(n) {
  const isGateway = n.connection_type === void 0 || String(n.role) === "master";
  const wired = Array.isArray(n.connection_type) && n.connection_type.includes("wired");
  const rawSignals = n.signal_level;
  const signals = rawSignals && typeof rawSignals === "object" && !Array.isArray(rawSignals) ? rawSignals : {};
  const weakest = wired || isGateway ? void 0 : weakestWireless(signals);
  let status;
  if (isGateway) status = "gateway";
  else if (wired) status = "wired (best)";
  else if (weakest === void 0) status = "wireless (unknown signal)";
  else if (weakest.level <= 1) status = `weak wireless (${weakest.band}=${weakest.level})`;
  else if (weakest.level === 2) status = "wireless (ok)";
  else status = "wireless (strong)";
  return {
    name: nodeName(n),
    mac: String(n.mac ?? "").toUpperCase(),
    role: n.role,
    status,
    uplink: isGateway ? "WAN" : wired ? "wired" : n.connection_type,
    signal_level: isGateway ? void 0 : signals,
    backhaul_speed: n.backhual_speed !== void 0 ? `${n.backhual_speed}/${n.backhual_max_speed} Mbps` : void 0,
    internet: n.inet_status,
    firmware: n.software_ver
  };
}
function guestSummary(wlan, clients) {
  const bands = {};
  let anyEnabled = false;
  const wlanObj = wlan && typeof wlan === "object" && !Array.isArray(wlan) ? wlan : {};
  for (const [band, cfg] of Object.entries(wlanObj)) {
    const guest = cfg && typeof cfg === "object" ? cfg.guest : void 0;
    if (!guest || typeof guest !== "object") continue;
    if (guest.enable) anyEnabled = true;
    bands[band] = {
      ssid: decodeName(guest.ssid),
      enabled: guest.enable ?? false,
      vlan_id: guest.vlan_id
    };
  }
  const guestClientCount = clients.filter(
    (c) => typeof c.interface === "string" && c.interface !== "" && c.interface !== "main"
  ).length;
  return { enabled: anyEnabled, bands, guestClientCount };
}
function bandMap(perNode) {
  const nodeSummaries = [];
  const clients = [];
  let band24 = 0;
  for (const { node, clients: list } of perNode) {
    const name = nodeName(node);
    const byBand = {};
    let wired = 0;
    for (const c of list) {
      const isWired = c.wire_type === "wired";
      const band = isWired ? "wired" : String(c.connection_type ?? "unknown");
      if (isWired) wired += 1;
      else if (band === "band2_4") band24 += 1;
      byBand[band] = (byBand[band] ?? 0) + 1;
      clients.push({
        name: decodeName(c.name),
        mac: String(c.mac ?? "").toUpperCase(),
        node: name,
        band: isWired ? "wired" : band
      });
    }
    nodeSummaries.push({ node: name, mac: String(node.mac ?? "").toUpperCase(), clientCount: list.length, wired, byBand });
  }
  return { nodes: nodeSummaries, clients, band24GhzClients: band24 };
}
function wiredPorts(nodes, nameByMac) {
  const perNode = [];
  let degraded = 0;
  for (const n of nodes) {
    const raw = n.wired_port_list;
    const ports = Array.isArray(raw) ? raw : [];
    if (ports.length === 0) continue;
    const entries = ports.map((p) => {
      const mac = String(p.mac ?? "").toUpperCase();
      const speed = Number(p.port_speed);
      const isDegraded = Number.isFinite(speed) && speed > 0 && speed < 1e3;
      if (isDegraded) degraded += 1;
      return {
        mac,
        name: nameByMac.get(mac),
        port: p.port,
        speed_mbps: Number.isFinite(speed) ? speed : p.port_speed,
        degraded: isDegraded
      };
    });
    perNode.push({ node: nodeName(n), mac: String(n.mac ?? "").toUpperCase(), ports: entries });
  }
  return { nodes: perNode, degradedLinks: degraded };
}
var DFS_CHANNELS = /* @__PURE__ */ new Set([52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144]);
function channelMap(wlan) {
  const wlanObj = wlan && typeof wlan === "object" && !Array.isArray(wlan) ? wlan : {};
  const bands = {};
  for (const [band, cfg] of Object.entries(wlanObj)) {
    const host = cfg?.host;
    const backhaul = cfg?.backhaul;
    if (!host) continue;
    const ch = Number(host.channel);
    bands[band] = {
      channel: host.channel,
      width: host.channel_width,
      mode: host.mode,
      backhaul_channel: backhaul?.channel,
      dfs: Number.isFinite(ch) && DFS_CHANNELS.has(ch)
    };
  }
  return bands;
}
function qosPriority(clients) {
  return clients.filter((c) => c.enable_priority === true).map((c) => ({
    name: decodeName(c.name),
    mac: String(c.mac ?? "").toUpperCase(),
    permanent: c.remain_time === -1,
    remain_time: c.remain_time
  }));
}

// src/deco/insights.ts
function bottlenecks(nodes, clientCountByMac) {
  const flagged = [];
  for (const n of nodes) {
    const h = nodeHealth(n);
    const status = String(h.status);
    if (!status.startsWith("weak wireless") && !status.startsWith("wireless")) continue;
    if (status.startsWith("wireless (strong)")) continue;
    const clients = clientCountByMac.get(h.mac) ?? 0;
    if (clients === 0) continue;
    const weak = status.startsWith("weak");
    flagged.push({
      node: h.name,
      mac: h.mac,
      backhaul: status,
      clients,
      // Weak backhaul with clients is worse than an ok one with clients.
      severity: weak ? "high" : clients >= 5 ? "medium" : "low"
    });
  }
  const rank = (s) => s === "high" ? 0 : s === "medium" ? 1 : 2;
  flagged.sort(
    (a, b) => rank(a.severity) - rank(b.severity) || Number(b.clients) - Number(a.clients)
  );
  return { bottlenecks: flagged, count: flagged.length };
}
function flag2(value) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value === 1;
  if (typeof value === "string") {
    if (value === "true" || value === "1") return true;
    if (value === "false" || value === "0") return false;
  }
  return void 0;
}
function enableOf(payload) {
  if (payload && typeof payload === "object" && !("error" in payload)) {
    return payload.enable;
  }
  return void 0;
}
function radioFeatures(beamforming, ieee80211r, operationMode, bandwidthEnhance) {
  const om = operationMode && typeof operationMode === "object" && !("error" in operationMode) ? operationMode : void 0;
  const ht160Raw = bandwidthEnhance && typeof bandwidthEnhance === "object" && !("error" in bandwidthEnhance) ? bandwidthEnhance.enable_ht160 : void 0;
  return {
    beamforming: flag2(enableOf(beamforming)),
    fast_roaming_80211r: flag2(enableOf(ieee80211r)),
    ht160: flag2(ht160Raw),
    radio_role: typeof om?.mode === "string" ? om.mode : void 0
  };
}
function networkAdvanced(vlan, igmp, macClone) {
  const vlanOk = vlan && typeof vlan === "object" && !("error" in vlan);
  const vlanInner = vlanOk ? vlan.vlan ?? vlan : void 0;
  const igmpEnable = igmp && typeof igmp === "object" && !("error" in igmp) ? igmp.enable_join_igmpv3 : void 0;
  return {
    vlan_enabled: flag2(vlanInner?.enable),
    igmp_snooping: flag2(igmpEnable),
    wan_mac_clone: flag2(enableOf(macClone))
  };
}
function signalBar(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : void 0;
}
function clientSignal(accessList, nameByMac) {
  const clients = [];
  let weak = 0;
  for (const c of accessList) {
    const mac = String(c.mac ?? "").toUpperCase();
    const wired = c.wire_type === "wired";
    const band = wired ? "wired" : String(c.connection_type ?? "unknown");
    const sig2 = signalBar(c.signal_level_2g);
    const sig5 = signalBar(c.signal_level_5g);
    const active = wired ? void 0 : band.includes("2") ? sig2 : sig5;
    const isWeak = !wired && typeof active === "number" && active <= 1;
    if (isWeak) weak += 1;
    clients.push({
      name: nameByMac.get(mac),
      mac,
      band,
      is_guest: c.is_guest === 1 || c.is_guest === true,
      signal_2g: sig2,
      signal_5g: sig5,
      active_signal: active,
      weak: isWeak
    });
  }
  const rank = (c) => c.band === "wired" ? 100 : typeof c.active_signal === "number" ? c.active_signal : 99;
  clients.sort((a, b) => rank(a) - rank(b));
  return { clients, count: clients.length, weakClients: weak };
}
function ethBackhaulPorts(nodes) {
  const out = [];
  for (const n of nodes) {
    const ports = n.eth_bkhl_ports;
    if (Array.isArray(ports) && ports.length > 0) {
      out.push({ mac: String(n.mac ?? "").toUpperCase(), backhaul_ports: ports });
    }
  }
  return out;
}

// src/tools.ts
var MAC = z.string().regex(/^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$/, "expected a MAC like AA:BB:CC:DD:EE:FF");
function registerTools(server, client, config) {
  const render = (summary, payload) => ({
    content: [
      {
        type: "text",
        text: `${summary}

${JSON.stringify(normalize(payload, config.revealSecrets), null, 2)}`
      }
    ]
  });
  const fail = (message) => ({
    content: [{ type: "text", text: message }],
    isError: true
  });
  const guard = async (run) => {
    try {
      return await run();
    } catch (err) {
      if (err instanceof DecoWriteBlockedError) return fail(err.message);
      if (err instanceof DecoLoginError) {
        return fail(
          `Deco rejected the credentials: ${err.message}
Check DECO_PASSWORD in .env. Do not retry blindly - the firmware locks the account after a handful of failed attempts.`
        );
      }
      if (err instanceof DecoTimeoutError) {
        return fail(`The Deco did not answer in time: ${err.message}`);
      }
      if (err instanceof DecoApiError) {
        return fail(`The Deco refused the request: ${err.message}`);
      }
      return fail(`Unexpected failure: ${err.message}`);
    }
  };
  server.registerTool(
    "deco_overview",
    {
      title: "Network overview",
      description: "One-shot health snapshot of the whole mesh: every Deco node with its role and firmware, how many clients are attached, internet status, and CPU/memory load. Start here when asked how the network is doing.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const nodes = await client.call({
        endpoint: "admin/device",
        form: "device_list",
        operation: "read"
      });
      const clients = await client.call({
        endpoint: "admin/client",
        form: "client_list",
        operation: "read",
        params: { device_mac: "default" }
      });
      const internet = await safely(
        () => client.call({ endpoint: "admin/network", form: "internet", operation: "read" })
      );
      const performance = await safely(
        () => client.call({
          endpoint: "admin/network",
          form: "performance",
          operation: "read"
        })
      );
      const nodeList = nodes.device_list ?? [];
      const nodeCount = nodeList.length;
      const clientCount = clients.client_list?.length ?? 0;
      const master = nodeList.find((n) => String(n.role ?? "").toLowerCase() === "master") ?? nodeList[0];
      const model = master ? `${master.device_model ?? "Deco"} fw ${master.software_ver ?? "?"}` : "unknown";
      return render(
        `${nodeCount} Deco node(s) (${model}), ${clientCount} connected client(s) on ${client.host}.`,
        { model, nodes: nodeList, clientCount, internet, performance }
      );
    })
  );
  server.registerTool(
    "deco_list_nodes",
    {
      title: "List mesh nodes",
      description: "The Deco units forming the mesh, with MAC, role (gateway or satellite), hardware and firmware version, and IP. For backhaul signal and topology use deco_node_health and deco_topology. Use this to find a node running old firmware.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const result = await client.call({
        endpoint: "admin/device",
        form: "device_list",
        operation: "read"
      });
      const list = result.device_list ?? [];
      return render(`${list.length} Deco node(s) in the mesh.`, list);
    })
  );
  server.registerTool(
    "deco_topology",
    {
      title: "Mesh topology tree",
      description: "The parent/child tree of the mesh: which node each satellite connects back through to reach the gateway. Built from each node's uplink, since the firmware's topology endpoint is app-only. Use it to see whether a satellite hangs off the gateway directly or chains through another node.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const result = await client.call({
        endpoint: "admin/device",
        form: "device_list",
        operation: "read"
      });
      const nodes = result.device_list ?? [];
      return render(`${nodes.length} node(s).`, buildTopology(nodes));
    })
  );
  server.registerTool(
    "deco_node_health",
    {
      title: "Backhaul and node health",
      description: "Per node: role, how it reaches the mesh (wired or which wireless bands), the backhaul signal level on each band (0 weak - 3 strong), negotiated backhaul speed where the firmware reports it, and internet status. Replaces the app-only signal-level view. Use it to find a satellite on a weak wireless uplink.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const result = await client.call({
        endpoint: "admin/device",
        form: "device_list",
        operation: "read"
      });
      const nodes = result.device_list ?? [];
      const report = nodes.map(nodeHealth);
      const weak = report.filter((r) => String(r.status).startsWith("weak")).length;
      const bridge = await safely(
        () => client.call({
          endpoint: "admin/wireless",
          form: "bridge",
          operation: "read"
        })
      );
      const placement = bridge && typeof bridge === "object" && "location" in bridge ? bridge.location : void 0;
      return render(
        `${report.length} node(s)${weak ? `, ${weak} on a weak wireless uplink` : ""}${placement ? `, placement: ${placement}` : ""}.`,
        { placement, nodes: report }
      );
    })
  );
  server.registerTool(
    "deco_guest_network",
    {
      title: "Guest network status",
      description: "Whether the guest Wi-Fi is on, its SSID and VLAN per band, and how many clients are on it right now. Guest settings live inside the Wi-Fi config (the dedicated guest endpoint is app-only), so this pulls them out and counts guest clients from the client list.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const wlan = await client.call({
        endpoint: "admin/wireless",
        form: "wlan",
        operation: "read"
      });
      const clients = await client.call({
        endpoint: "admin/client",
        form: "client_list",
        operation: "read",
        params: { device_mac: "default" }
      });
      const summary = guestSummary(wlan, clients.client_list ?? []);
      return render(
        summary.enabled ? `Guest network is ON, ${summary.guestClientCount} client(s) connected.` : "Guest network is off.",
        summary
      );
    })
  );
  server.registerTool(
    "deco_band_map",
    {
      title: "Client band and node map",
      description: "Which node each client is on and on which band (wired, 2.4 GHz, 5 GHz), with a per-node breakdown. Reads the client list per node. Use it to see how load spreads across the mesh and how many clients sit on 2.4 GHz \u2014 candidates to move to 5 GHz. Pair it with deco_client_signal to see each client's actual signal and confirm a move would help.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const nodesResult = await client.call({
        endpoint: "admin/device",
        form: "device_list",
        operation: "read"
      });
      const nodes = nodesResult.device_list ?? [];
      const perNode = [];
      for (const node of nodes) {
        const r = await client.call({
          endpoint: "admin/client",
          form: "client_list",
          operation: "read",
          params: { device_mac: node.mac }
        });
        perNode.push({ node, clients: r.client_list ?? [] });
      }
      const map = bandMap(perNode);
      return render(
        `${map.clients.length} client(s) across ${nodes.length} node(s), ${map.band24GhzClients} on 2.4 GHz.`,
        map
      );
    })
  );
  server.registerTool(
    "deco_client_signal",
    {
      title: "Per-client Wi-Fi signal",
      description: "Each wireless client's signal strength on the band it is using (0 weak - 3 strong), weakest first, flagging weak links. Reads the client_access form, the only per-client signal this firmware exposes \u2014 it complements deco_band_map, which shows band and node but not signal. Hostnames are joined in from the client list; wired clients carry no signal.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const access = await client.call({
        endpoint: "admin/client",
        form: "client_access",
        operation: "read"
      });
      const identity = await safely(
        () => client.call({
          endpoint: "admin/client",
          form: "client_list",
          operation: "read",
          params: { device_mac: "default" }
        })
      );
      const nameByMac = /* @__PURE__ */ new Map();
      const idList = identity && typeof identity === "object" && !("error" in identity) ? identity.client_list ?? [] : [];
      for (const c of idList) {
        const name = decodeName(c.name);
        if (typeof name === "string" && name) {
          nameByMac.set(String(c.mac ?? "").toUpperCase(), name);
        }
      }
      const result = clientSignal(access.client_list ?? [], nameByMac);
      return render(
        `${result.count} client(s), ${result.weakClients} with weak signal.`,
        result
      );
    })
  );
  server.registerTool(
    "deco_wired_ports",
    {
      title: "Wired connections and link speeds",
      description: "The devices seen on each node's wired ports, with the negotiated link speed, flagging anything below gigabit. Use it to find a device stuck on a 100 Mbps link (a bad cable or port).",
      inputSchema: {}
    },
    async () => guard(async () => {
      const nodesResult = await client.call({
        endpoint: "admin/device",
        form: "device_list",
        operation: "read"
      });
      const clientsResult = await client.call({
        endpoint: "admin/client",
        form: "client_list",
        operation: "read",
        params: { device_mac: "default" }
      });
      const nameByMac = /* @__PURE__ */ new Map();
      for (const c of clientsResult.client_list ?? []) {
        const name = decodeName(c.name);
        if (typeof name === "string" && name) {
          nameByMac.set(String(c.mac ?? "").toUpperCase(), name);
        }
      }
      const nodes = nodesResult.device_list ?? [];
      const result = wiredPorts(nodes, nameByMac);
      const backhaul = ethBackhaulPorts(nodes);
      const nodeCount = result.nodes.length;
      return render(
        `Wired devices on ${nodeCount} node(s)${result.degradedLinks ? `, ${result.degradedLinks} below gigabit` : ""}.`,
        { ...result, ethernet_backhaul: backhaul }
      );
    })
  );
  server.registerTool(
    "deco_channel_map",
    {
      title: "Channels, width and mode per band",
      description: "The channel, channel width and mode each radio band is on, plus the backhaul channel, flagging any band parked on a DFS channel (which can drop out when nearby radar is detected). Use it when Wi-Fi stutters periodically or to check for channel overlap.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const wlan = await client.call({
        endpoint: "admin/wireless",
        form: "wlan",
        operation: "read"
      });
      const bands = channelMap(wlan);
      const dfs = Object.entries(bands).filter(([, b]) => b.dfs).map(([name]) => name);
      return render(
        dfs.length ? `Bands on a DFS channel: ${dfs.join(", ")}.` : "Channels per band.",
        bands
      );
    })
  );
  server.registerTool(
    "deco_qos_priority",
    {
      title: "Devices with QoS priority",
      description: "Clients that have a QoS priority set (the app's device-priority feature), and whether it is permanent or counting down. Read-only view; the firmware does not expose setting priority over the local API.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const result = await client.call({
        endpoint: "admin/client",
        form: "client_list",
        operation: "read",
        params: { device_mac: "default" }
      });
      const prioritised = qosPriority(result.client_list ?? []);
      return render(`${prioritised.length} device(s) with QoS priority.`, prioritised);
    })
  );
  server.registerTool(
    "deco_bottlenecks",
    {
      title: "Mesh bottlenecks",
      description: "Satellites on a wireless (especially weak) backhaul that are carrying clients \u2014 those clients share the node's constrained uplink, so this is where moving devices or cabling the node helps most. Ranked worst first. Empty when every node is wired or unloaded.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const nodesResult = await client.call({
        endpoint: "admin/device",
        form: "device_list",
        operation: "read"
      });
      const nodes = nodesResult.device_list ?? [];
      const countByMac = /* @__PURE__ */ new Map();
      for (const node of nodes) {
        const r = await client.call({
          endpoint: "admin/client",
          form: "client_list",
          operation: "read",
          params: { device_mac: node.mac }
        });
        countByMac.set(String(node.mac ?? "").toUpperCase(), r.client_list?.length ?? 0);
      }
      const result = bottlenecks(nodes, countByMac);
      return render(
        result.count ? `${result.count} bottleneck(s): wireless node(s) carrying clients.` : "No bottlenecks: every loaded node has a good backhaul.",
        result
      );
    })
  );
  server.registerTool(
    "deco_radio_features",
    {
      title: "Advanced radio features",
      description: "The Advanced-screen radio toggles: beamforming, 802.11r fast roaming, 160 MHz channel width (HT160), and the radio operation role. Read-only. Note radio_role is the radios' role, not the system Router/AP mode \u2014 use deco_operating_mode for that.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const beamforming = await safely(
        () => client.call({ endpoint: "admin/wireless", form: "beamforming", operation: "read" })
      );
      const ieee80211r = await safely(
        () => client.call({ endpoint: "admin/wireless", form: "ieee80211r", operation: "read" })
      );
      const operationMode = await safely(
        () => client.call({ endpoint: "admin/wireless", form: "operation_mode", operation: "read" })
      );
      const bandwidthEnhance = await safely(
        () => client.call({ endpoint: "admin/wireless", form: "bandwidth_enhance", operation: "read" })
      );
      return render(
        "Advanced radio features.",
        radioFeatures(beamforming, ieee80211r, operationMode, bandwidthEnhance)
      );
    })
  );
  server.registerTool(
    "deco_network_advanced",
    {
      title: "Advanced network status",
      description: "VLAN, IGMP snooping (multicast/IPTV) and WAN MAC clone \u2014 the advanced network toggles the app exposes. Read-only status.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const vlan = await safely(
        () => client.call({ endpoint: "admin/network", form: "vlan", operation: "read" })
      );
      const igmp = await safely(
        () => client.call({ endpoint: "admin/network", form: "igmp_setting", operation: "read" })
      );
      const macClone = await safely(
        () => client.call({ endpoint: "admin/network", form: "mac_clone", operation: "read" })
      );
      return render("Advanced network status.", networkAdvanced(vlan, igmp, macClone));
    })
  );
  server.registerTool(
    "deco_list_clients",
    {
      title: "List connected clients",
      description: "Devices currently connected to the mesh: name, MAC, IP, which Deco node they are on, wired or wireless, band, and live throughput. Pass node_mac to narrow it to a single Deco unit.",
      inputSchema: {
        node_mac: MAC.optional().describe(
          "Only list clients on this Deco node. Omit for the whole mesh."
        )
      }
    },
    async ({ node_mac }) => guard(async () => {
      const result = await client.call({
        endpoint: "admin/client",
        form: "client_list",
        operation: "read",
        params: { device_mac: node_mac ?? "default" }
      });
      const list = result.client_list ?? [];
      const enriched = list.map((entry) => ({
        ...entry,
        down_rate: humanRate(entry.down_speed),
        up_rate: humanRate(entry.up_speed)
      }));
      const scope = node_mac ? `node ${node_mac}` : "the mesh";
      return render(`${list.length} client(s) connected to ${scope}.`, enriched);
    })
  );
  server.registerTool(
    "deco_network_status",
    {
      title: "Internet and WAN status",
      description: "WAN link state, public IP, connection type, DNS, IPv6 and the LAN subnet. Use this when diagnosing whether a problem is upstream of the router.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const internet = await safely(
        () => client.call({ endpoint: "admin/network", form: "internet", operation: "read" })
      );
      const wan = await safely(
        () => client.call({ endpoint: "admin/network", form: "wan_ipv4", operation: "read" })
      );
      const lan = await safely(
        () => client.call({ endpoint: "admin/network", form: "lan_ipv4", operation: "read" })
      );
      const wanDhcp = await safely(
        () => client.call({ endpoint: "admin/network", form: "dhcp_dial", operation: "read" })
      );
      const wanMode = await safely(
        () => client.call({ endpoint: "admin/network", form: "wan_mode", operation: "read" })
      );
      return render("WAN and LAN status.", { internet, wan, lan, wanDhcp, wanMode });
    })
  );
  server.registerTool(
    "deco_wifi_settings",
    {
      title: "Read Wi-Fi configuration",
      description: "SSIDs, bands, security mode and whether each radio is enabled. Passphrases are redacted unless the server was started with DECO_REVEAL_SECRETS=1.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const wlan = await client.call({
        endpoint: "admin/wireless",
        form: "wlan",
        operation: "read"
      });
      return render("Wi-Fi configuration.", wlan);
    })
  );
  server.registerTool(
    "deco_performance",
    {
      title: "Mesh CPU and memory load",
      description: "CPU and memory utilisation, reported for the mesh as a whole rather than per node. Useful when the mesh feels slow but the WAN is healthy.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const perf = await client.call({
        endpoint: "admin/network",
        form: "performance",
        operation: "read"
      });
      return render("Mesh CPU and memory load.", perf);
    })
  );
  server.registerTool(
    "deco_client_traffic",
    {
      title: "Per-client throughput snapshot",
      description: "Instantaneous upload and download rate per client \u2014 a snapshot at the moment of the call, NOT a cumulative byte total. Sample repeatedly to see who is using bandwidth right now. deco_list_clients returns the same rates humanised alongside each client.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const stats = await client.call({
        endpoint: "admin/client",
        form: "traffic_stat",
        operation: "list"
      });
      return render("Traffic per client.", stats);
    })
  );
  server.registerTool(
    "deco_blocklist",
    {
      title: "List blocked clients",
      description: "Clients currently denied access to the network.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const result = await client.call({
        endpoint: "admin/client",
        form: "black_list",
        operation: "list"
      });
      return render("Blocked clients.", result);
    })
  );
  server.registerTool(
    "deco_reservations",
    {
      title: "List DHCP reservations",
      description: "Static IP address reservations held by the Deco's DHCP server: which MAC always gets which address, and how many slots remain.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const result = await client.call({
        endpoint: "admin/client",
        form: "addr_reservation",
        operation: "getlist"
      });
      const used = result.reservation_list?.length ?? 0;
      const max = result.reservation_list_max_count;
      const capacity = max ? ` (${used}/${max} slots used, ${max - used} free)` : "";
      return render(`${used} reservation(s)${capacity}.`, result);
    })
  );
  server.registerTool(
    "deco_leases",
    {
      title: "DHCP lease table (synthesized)",
      description: "A lease-table view of the network: every device with its IP, MAC, name, whether the address is STATIC (a DHCP reservation) or DYNAMIC, and whether it is online now. This firmware does not expose the raw DHCP lease table (it is app-only), so this merges the connected-client list with the reservation table \u2014 there are no lease expiry times, and a device that is offline shows only if it holds a reservation.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const clientsResult = await client.call({
        endpoint: "admin/client",
        form: "client_list",
        operation: "read",
        params: { device_mac: "default" }
      });
      const reservationsResult = await client.call({ endpoint: "admin/client", form: "addr_reservation", operation: "getlist" });
      const clients = clientsResult.client_list ?? [];
      const reservations = reservationsResult.reservation_list ?? [];
      const reservedByMac = new Map(
        reservations.map((r) => [r.mac.toUpperCase(), r.ip])
      );
      const onlineMacs = new Set(
        clients.map((c) => String(c.mac ?? "").toUpperCase())
      );
      const leases = clients.map((c) => {
        const mac = String(c.mac ?? "").toUpperCase();
        return {
          ip: c.ip,
          mac,
          name: normalize(c.name, false),
          type: reservedByMac.has(mac) ? "static" : "dynamic",
          online: true,
          connection: c.wire_type === "wired" ? "wired" : c.connection_type
        };
      });
      for (const r of reservations) {
        if (!onlineMacs.has(r.mac.toUpperCase())) {
          leases.push({
            ip: r.ip,
            mac: r.mac.toUpperCase(),
            name: void 0,
            type: "static",
            online: false,
            connection: void 0
          });
        }
      }
      leases.sort(
        (a, b) => String(a.ip ?? "").localeCompare(String(b.ip ?? ""), void 0, { numeric: true })
      );
      const staticCount = leases.filter((l) => l.type === "static").length;
      const offline = leases.filter((l) => !l.online).length;
      return render(
        `${leases.length} entries: ${staticCount} static, ${leases.length - staticCount} dynamic, ${offline} reserved-but-offline.`,
        leases
      );
    })
  );
  server.registerTool(
    "deco_speedtest_result",
    {
      title: "Last speed test result",
      description: "The most recent WAN speed test the Deco ran, with download and upload rate and when it was measured. Reads a stored result; it does not start a new test.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const result = await client.call({
        endpoint: "admin/device",
        form: "speedtest",
        operation: "read"
      });
      return render("Last speed test.", result);
    })
  );
  server.registerTool(
    "deco_operating_mode",
    {
      title: "Router or Access Point mode",
      description: "Whether the mesh runs as a full Router (its own DHCP and NAT) or as an Access Point behind another router. Answers the double-NAT question directly.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const mode = await client.call({
        endpoint: "admin/device",
        form: "mode",
        operation: "read"
      });
      return render("Operating mode.", mode);
    })
  );
  server.registerTool(
    "deco_time_settings",
    {
      title: "Timezone and clock",
      description: "The router's timezone, current date and time, and DST state. Useful when log timestamps or scheduled rules look off.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const time = await client.call({
        endpoint: "admin/device",
        form: "timesetting",
        operation: "read"
      });
      return render("Time settings.", time);
    })
  );
  server.registerTool(
    "deco_show_performance",
    {
      title: "Performance page availability",
      description: "Whether this firmware exposes the performance/insights page in its UI, read from the component_control switch list. A capability flag, not live load \u2014 use deco_performance for actual CPU and memory figures.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const flags = await client.call({
        endpoint: "admin/component_control",
        form: "switch_list",
        operation: "read"
      });
      return render("Web-UI component flags.", flags);
    })
  );
  server.registerTool(
    "deco_capabilities",
    {
      title: "What this Deco actually serves",
      description: "Probe the device to report its model and firmware, and which catalogued read endpoints it actually serves. The catalogue is a superset across Deco models; this tells you what works on THIS unit. Run it first on an unfamiliar Deco. Makes one read call per endpoint, nothing mutating.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const report = await probeCapabilities(client);
      writeState("capabilities", { ...report, host: client.host });
      const model = report.device ? `${report.device.model} fw ${report.device.firmware}` : "unknown device";
      return render(
        `${model}: ${report.served.length} of ${report.served.length + report.unserved.length} catalogued read endpoints served.`,
        report
      );
    })
  );
  server.registerTool(
    "deco_track_clients",
    {
      title: "Save a client baseline",
      description: "Snapshot the currently connected devices as a baseline, so a later deco_whats_new can report what has appeared or left since. Call this when the network is in a known-good state.",
      inputSchema: {}
    },
    async () => guard(async () => {
      const result = await client.call({
        endpoint: "admin/client",
        form: "client_list",
        operation: "read",
        params: { device_mac: "default" }
      });
      const list = result.client_list ?? [];
      const baseline = list.map((c) => ({
        mac: String(c.mac ?? ""),
        name: normalize(c.name, false),
        ip: c.ip
      }));
      const file = writeState("client-baseline", { host: client.host, clients: baseline });
      return render(`Baseline saved: ${baseline.length} client(s).`, {
        count: baseline.length,
        file
      });
    })
  );
  server.registerTool(
    "deco_whats_new",
    {
      title: "What changed since the baseline",
      description: "Compare the currently connected devices against the last deco_track_clients baseline and report which MACs are new and which have left. This is the automated form of 'is there anything on my network I don't recognise'. Save a baseline first, or this has nothing to compare.",
      inputSchema: {
        update: z.boolean().optional().describe("Also replace the baseline with the current state. Default false.")
      }
    },
    async ({ update }) => guard(async () => {
      const baseline = readState(
        "client-baseline"
      );
      if (!baseline) {
        return fail(
          "No baseline saved yet. Run deco_track_clients first to record one."
        );
      }
      const result = await client.call({
        endpoint: "admin/client",
        form: "client_list",
        operation: "read",
        params: { device_mac: "default" }
      });
      const current = (result.client_list ?? []).map((c) => ({
        mac: String(c.mac ?? ""),
        name: normalize(c.name, false),
        ip: c.ip
      }));
      const baseMacs = new Set(baseline.clients.map((c) => c.mac));
      const currentMacs = new Set(current.map((c) => c.mac));
      const arrived = current.filter((c) => !baseMacs.has(c.mac));
      const departed = baseline.clients.filter((c) => !currentMacs.has(c.mac));
      if (update) {
        writeState("client-baseline", { host: client.host, clients: current });
      }
      const summary = arrived.length === 0 && departed.length === 0 ? "No change since the baseline." : `${arrived.length} new, ${departed.length} gone since the baseline.`;
      return render(summary, { arrived, departed, currentCount: current.length });
    })
  );
  if (config.allowWrite) {
    server.registerTool(
      "deco_block_client",
      {
        title: "Block a client",
        description: "Deny a device access to the network by MAC address. NOT SUPPORTED on Deco M9 Plus firmware 1.9.1: every parameter shape was rejected with error_code 1 even for a currently-connected client, and this firmware's web UI has no blocking feature - it is app-only (HomeShield / parental controls). Kept for Deco models that serve the app form set. If it returns error_code 1, blocking is unavailable on this firmware; report that and use the Deco app, do not retry.",
        inputSchema: {
          mac: MAC.describe("MAC address of the client to block."),
          name: z.string().optional().describe("Label stored alongside the block entry.")
        }
      },
      async ({ mac, name }) => guard(async () => {
        const cmac = canonicalMac(mac);
        await client.call({
          endpoint: "admin/client",
          form: "black_list",
          operation: "add",
          params: {
            client_mac: cmac,
            ...name ? { name: Buffer.from(name, "utf8").toString("base64") } : {}
          }
        });
        return render(`Blocked ${cmac}.`, { mac: cmac, blocked: true });
      })
    );
    server.registerTool(
      "deco_unblock_client",
      {
        title: "Unblock a client",
        description: "Restore network access for a previously blocked MAC address.",
        inputSchema: { mac: MAC.describe("MAC address of the client to unblock.") }
      },
      async ({ mac }) => guard(async () => {
        const cmac = canonicalMac(mac);
        await client.call({
          endpoint: "admin/client",
          form: "black_list",
          operation: "remove",
          params: { client_mac: cmac }
        });
        return render(`Unblocked ${cmac}.`, { mac: cmac, blocked: false });
      })
    );
    server.registerTool(
      "deco_reserve_address",
      {
        title: "Add a DHCP reservation",
        description: "Pin a MAC address to a fixed IP, so the device always gets the same address. The IP must be inside the LAN subnet and not already leased or reserved - the firmware rejects collisions with IP_CONFLICT. Check deco_reservations and deco_list_clients first.",
        inputSchema: {
          mac: MAC.describe(
            "MAC address of the device. The firmware stores these dash-separated and uppercase, e.g. A4-50-46-4A-29-0B."
          ),
          ip: z.string().regex(/^(\d{1,3}\.){3}\d{1,3}$/, "expected an IPv4 address").describe("IPv4 address to reserve, inside the LAN subnet.")
        }
      },
      async ({ mac, ip }) => guard(async () => {
        const cmac = canonicalMac(mac);
        await client.call({
          endpoint: "admin/client",
          form: "addr_reservation",
          operation: "add",
          // add takes an array of objects; remove takes an array of bare MAC
          // strings. The asymmetry is the firmware's, not a typo.
          params: { reservation_list: [{ mac: cmac, ip }] }
        });
        return render(`Reserved ${ip} for ${cmac}.`, { mac: cmac, ip });
      })
    );
    server.registerTool(
      "deco_remove_reservation",
      {
        title: "Remove a DHCP reservation",
        description: "Drop a static IP reservation. The device goes back to a dynamic address at its next lease renewal.",
        inputSchema: { mac: MAC.describe("MAC address whose reservation to drop.") }
      },
      async ({ mac }) => guard(async () => {
        const cmac = canonicalMac(mac);
        await client.call({
          endpoint: "admin/client",
          form: "addr_reservation",
          operation: "remove",
          params: { reservation_list: [cmac] }
        });
        return render(`Removed the reservation for ${cmac}.`, { mac: cmac });
      })
    );
    server.registerTool(
      "deco_modify_reservation",
      {
        title: "Change a reservation's IP",
        description: "Point an existing DHCP reservation at a different IP. The firmware has no modify operation, so this removes the old reservation and adds the new one; if the add fails the old one is restored. The new IP must be in the LAN subnet and not already leased or reserved.",
        inputSchema: {
          mac: MAC.describe("MAC address whose reservation to move."),
          ip: z.string().regex(/^(\d{1,3}\.){3}\d{1,3}$/, "expected an IPv4 address").describe("New IPv4 address to reserve.")
        }
      },
      async ({ mac, ip }) => guard(
        async () => (
          // The whole getlist→remove→add sequence runs under one lock, so no
          // other call can touch the session (or the reservation) between steps.
          client.transaction(async (run) => {
            const cmac = canonicalMac(mac);
            const current = await run({
              endpoint: "admin/client",
              form: "addr_reservation",
              operation: "getlist"
            });
            const existing = (current.reservation_list ?? []).find(
              (r) => canonicalMac(r.mac) === cmac
            );
            if (!existing) {
              return fail(
                `No reservation exists for ${cmac}. Use deco_reserve_address to create one.`
              );
            }
            const oldIp = existing.ip;
            if (oldIp === ip) {
              return render(`Already reserved at ${ip}; no change.`, { mac: cmac, ip });
            }
            await run({
              endpoint: "admin/client",
              form: "addr_reservation",
              operation: "remove",
              params: { reservation_list: [cmac] }
            });
            try {
              await run({
                endpoint: "admin/client",
                form: "addr_reservation",
                operation: "add",
                params: { reservation_list: [{ mac: cmac, ip }] }
              });
            } catch (addErr) {
              try {
                await run({
                  endpoint: "admin/client",
                  form: "addr_reservation",
                  operation: "add",
                  params: { reservation_list: [{ mac: cmac, ip: oldIp }] }
                });
              } catch {
                return fail(
                  `MOVE FAILED AND ROLLBACK FAILED: the reservation for ${cmac} is now GONE (was ${oldIp}, tried ${ip}). Re-create it with deco_reserve_address at ${oldIp}.`
                );
              }
              return fail(
                `Move failed: could not reserve ${ip} for ${cmac} (${addErr.message}). The original reservation at ${oldIp} has been restored.`
              );
            }
            return render(`Moved ${cmac} from ${oldIp} to ${ip}.`, {
              mac: cmac,
              from: oldIp,
              to: ip
            });
          })
        )
      )
    );
    server.registerTool(
      "deco_run_speedtest",
      {
        title: "Run a WAN speed test",
        description: "Start a speed test on the gateway, wait for it to finish, and return the measured download and upload rate in one call. It briefly saturates the WAN link, so avoid running it while someone is on a call. Takes up to about a minute.",
        inputSchema: {}
      },
      async () => guard(async () => {
        await client.call({
          endpoint: "admin/device",
          form: "speedtest",
          operation: "write"
        });
        const deadline = Date.now() + 9e4;
        let last = {};
        while (Date.now() < deadline) {
          await sleep(4e3);
          last = await client.call({
            endpoint: "admin/device",
            form: "speedtest",
            operation: "read"
          });
          const status = String(last.status ?? "");
          if (status && status !== "testing" && status !== "running") break;
        }
        return render("Speed test complete.", last);
      })
    );
    server.registerTool(
      "deco_speedtest_stop",
      {
        title: "Stop a running speed test",
        description: "Abort a speed test in progress and free the WAN link. Safe to call even if none is running.",
        inputSchema: {}
      },
      async () => guard(async () => {
        await client.call({
          endpoint: "admin/device",
          form: "speedtest",
          operation: "stop"
        });
        return render("Speed test stopped.", { stopped: true });
      })
    );
  }
  if (config.allowWrite && config.allowDangerous) {
    server.registerTool(
      "deco_reboot_node",
      {
        title: "Reboot Deco nodes",
        description: "Reboot one or more Deco units. Every client on those units loses connectivity for a couple of minutes; rebooting the gateway takes the whole network down. Confirm with the operator before calling this.",
        inputSchema: {
          macs: z.array(MAC).min(1).describe("MAC addresses of the Deco nodes to reboot.")
        }
      },
      async ({ macs }) => guard(async () => {
        await client.call({
          endpoint: "admin/device",
          form: "system",
          operation: "reboot",
          params: { mac_list: macs.map((mac) => ({ mac })) }
        });
        return render(`Reboot issued for ${macs.length} node(s).`, { macs });
      })
    );
  }
  server.registerTool(
    "deco_call",
    {
      title: "Call a catalogued Deco endpoint",
      description: "Direct access to any endpoint in the catalogue, for reads the dedicated tools do not cover. Call deco_catalog first to see what exists. Endpoints outside the catalogue are refused.",
      inputSchema: {
        endpoint: z.string().describe("Controller path under the session token, e.g. admin/wireless."),
        form: z.string().describe("The form name, e.g. wlan."),
        operation: z.string().describe("Operation name, e.g. read."),
        params: z.record(z.string(), z.unknown()).optional().describe("Payload params, passed through unchanged.")
      }
    },
    async ({ endpoint, form, operation, params }) => guard(async () => {
      const resolved = resolve(endpoint, form, operation);
      if ("error" in resolved) return fail(resolved.error);
      const decision = gate(resolved.risk, config);
      if (!decision.allowed) {
        if (decision.reason === "dangerous-blocked") {
          throw new DecoWriteBlockedError(
            `${endpoint}?form=${form} operation "${operation}" is disruptive (${resolved.summary}). Restart with DECO_ALLOW_DANGEROUS=1 to permit it.`
          );
        }
        throw new DecoWriteBlockedError(
          `${endpoint}?form=${form} operation "${operation}" changes configuration, but this server is running read-only. Restart it with DECO_ALLOW_WRITE=1 to permit writes.`
        );
      }
      const result = await client.call({ endpoint, form, operation, params });
      return render(`${endpoint}?form=${form} (${operation}, ${resolved.risk}).`, result);
    })
  );
  server.registerTool(
    "deco_catalog",
    {
      title: "List reachable endpoints",
      description: "Every endpoint deco_call will accept, with its operations and how disruptive each one is. No network traffic; reads the local catalogue.",
      inputSchema: {}
    },
    async () => {
      const entries = CATALOG.map((entry) => ({
        endpoint: entry.endpoint,
        form: entry.form,
        operations: entry.operations,
        summary: entry.summary
      }));
      const mode = config.allowDangerous ? "read + write + disruptive" : config.allowWrite ? "read + write" : "read-only";
      return render(`${entries.length} endpoints. Server mode: ${mode}.`, entries);
    }
  );
}
function sleep(ms) {
  return new Promise((resolve2) => setTimeout(resolve2, ms));
}
async function safely(run) {
  try {
    return await run();
  } catch (err) {
    return { error: err.message };
  }
}

// src/dotenv.ts
import fs2 from "node:fs";
import path2 from "node:path";
import { fileURLToPath as fileURLToPath2 } from "node:url";
function loadDotEnv(explicitPath) {
  const candidate = explicitPath ?? findDefaultEnvPath();
  if (!candidate || !fs2.existsSync(candidate)) return;
  let contents;
  try {
    contents = fs2.readFileSync(candidate, "utf8");
  } catch {
    return;
  }
  for (const line of contents.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const separator = trimmed.indexOf("=");
    if (separator <= 0) continue;
    const key = trimmed.slice(0, separator).trim();
    let value = trimmed.slice(separator + 1).trim();
    const quoted = value.length >= 2 && (value.startsWith('"') && value.endsWith('"') || value.startsWith("'") && value.endsWith("'"));
    if (quoted) value = value.slice(1, -1);
    if (process.env[key] === void 0) process.env[key] = value;
  }
}
function findDefaultEnvPath() {
  if (process.env.DECO_ENV_FILE) return process.env.DECO_ENV_FILE;
  try {
    const here = path2.dirname(fileURLToPath2(import.meta.url));
    return path2.resolve(here, "..", ".env");
  } catch {
    return null;
  }
}

// src/index.ts
async function main() {
  loadDotEnv();
  const config = loadConfig();
  const client = new DecoClient({
    host: config.host,
    username: config.username,
    password: config.password,
    timeoutMs: config.timeoutMs,
    rejectUnauthorized: config.rejectUnauthorized
  });
  const server = new McpServer(
    { name: "deco-mcp", version: "0.1.1" },
    {
      instructions: "Manages TP-Link Deco mesh routers over the local API. Start with deco_overview for a health snapshot. Wi-Fi passphrases are redacted by default. Mutating tools appear only when the operator has enabled them, so the tool list is an accurate statement of what you may do. Rebooting a node disconnects its clients - confirm with the operator before doing it."
    }
  );
  registerTools(server, client, config);
  const mode = config.allowDangerous ? "read+write+disruptive" : config.allowWrite ? "read+write" : "read-only";
  console.error(`deco-mcp: ${config.host} as "${config.username}" (${mode})`);
  const transport = new StdioServerTransport();
  await server.connect(transport);
  const shutdown = () => {
    void client.logout().finally(() => process.exit(0));
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}
main().catch((err) => {
  console.error(`deco-mcp failed to start: ${err.message}`);
  process.exit(1);
});
