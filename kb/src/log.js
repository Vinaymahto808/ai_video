// Structured JSON-lines logger. Redacts anything that looks like a key or token.
const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };
const threshold = LEVELS[process.env.KB_LOG_LEVEL ?? "info"] ?? 20;
const SECRET_KEY = /(api[_-]?key|secret|token|password|authorization)/i;

function redact(obj) {
  if (!obj || typeof obj !== "object") return obj;
  const out = Array.isArray(obj) ? [] : {};
  for (const [k, v] of Object.entries(obj)) {
    if (SECRET_KEY.test(k)) out[k] = "[redacted]";
    else if (v && typeof v === "object" && !(v instanceof Error)) out[k] = redact(v);
    else if (v instanceof Error) out[k] = { name: v.name, message: v.message };
    else out[k] = v;
  }
  return out;
}

function emit(level, msg, fields) {
  if (LEVELS[level] < threshold) return;
  const line = { t: new Date().toISOString(), level, msg, ...redact(fields ?? {}) };
  (level === "error" || level === "warn" ? process.stderr : process.stdout).write(JSON.stringify(line) + "\n");
}

export const log = {
  debug: (m, f) => emit("debug", m, f),
  info: (m, f) => emit("info", m, f),
  warn: (m, f) => emit("warn", m, f),
  error: (m, f) => emit("error", m, f),
  child: (base) => ({
    debug: (m, f) => emit("debug", m, { ...base, ...f }),
    info: (m, f) => emit("info", m, { ...base, ...f }),
    warn: (m, f) => emit("warn", m, { ...base, ...f }),
    error: (m, f) => emit("error", m, { ...base, ...f }),
  }),
};
