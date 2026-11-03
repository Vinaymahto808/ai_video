/** Minimal event emitter (no deps). `on("*", (type, payload) => …)` receives everything. */
export class Emitter {
  #map = new Map();
  on(type, fn) {
    if (!this.#map.has(type)) this.#map.set(type, new Set());
    this.#map.get(type).add(fn);
    return () => this.off(type, fn);
  }
  once(type, fn) {
    const off = this.on(type, (...a) => { off(); fn(...a); });
    return off;
  }
  off(type, fn) { this.#map.get(type)?.delete(fn); }
  emit(type, payload) {
    for (const fn of this.#map.get(type) ?? []) {
      try { fn(payload); } catch (e) { console.warn("[avatar-widget] listener for " + type + " threw", e); }
    }
    for (const fn of this.#map.get("*") ?? []) {
      try { fn(type, payload); } catch { /* ignore */ }
    }
  }
}
