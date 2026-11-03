// Heading-aware chunking. Blocks are grouped under their heading path; long blocks are split at
// paragraph then sentence boundaries with overlap; tables are kept whole where possible.
import { sha256 } from "../extract/extract.js";

const HEADING_RE = /^(#{1,3})\s+(.*)$/;

function splitLong(text, size, overlap) {
  if (text.length <= size) return [text];
  const sentences = text.split(/(?<=[.!?।])\s+(?=[A-Z0-9ऀ-ॿ"'(])/);
  const out = [];
  let cur = "";
  for (const s of sentences) {
    if (s.length > size) {
      // a single giant "sentence" (e.g. a run-on list): hard wrap on words
      if (cur) { out.push(cur.trim()); cur = ""; }
      const words = s.split(/\s+/);
      let w = "";
      for (const word of words) {
        if ((w + " " + word).length > size) { out.push(w.trim()); w = w.slice(-overlap) + " " + word; } else w += " " + word;
      }
      if (w.trim()) cur = w.trim();
      continue;
    }
    if ((cur + " " + s).length > size && cur) {
      out.push(cur.trim());
      cur = cur.slice(-overlap).trimStart() + " " + s;
    } else cur += (cur ? " " : "") + s;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

/**
 * @param {string} markdown cleaned page markdown
 * @param {{size?:number, overlap?:number, title?:string}} opts
 * @returns {{index:number, heading:string, text:string, hash:string, chars:number}[]}
 */
export function chunkMarkdown(markdown, { size = 1200, overlap = 200, title = "" } = {}) {
  const lines = markdown.split("\n");
  const path = []; // heading stack by level
  const sections = []; // {heading, paragraphs:[]}
  let cur = { heading: title, paragraphs: [] };
  let para = [];
  let inTable = false;

  const flushPara = () => {
    if (para.length) {
      cur.paragraphs.push({ text: para.join(inTable ? "\n" : " ").trim(), table: inTable });
      para = [];
    }
    inTable = false;
  };

  for (const line of lines) {
    const h = line.match(HEADING_RE);
    if (h) {
      flushPara();
      if (cur.paragraphs.length) sections.push(cur);
      const level = h[1].length;
      path.length = level - 1;
      path[level - 1] = h[2].trim();
      cur = { heading: path.filter(Boolean).join(" > "), paragraphs: [] };
      continue;
    }
    const isTableLine = line.trim().startsWith("|");
    if (line.trim() === "" ) { flushPara(); continue; }
    if (isTableLine !== inTable && para.length) flushPara();
    inTable = isTableLine;
    para.push(line.trim());
  }
  flushPara();
  if (cur.paragraphs.length) sections.push(cur);

  const chunks = [];
  for (const sec of sections) {
    const prefix = sec.heading ? `${sec.heading}\n\n` : "";
    let buf = "";
    const push = (body) => {
      const text = (prefix + body).trim();
      if (text.replace(/\s+/g, "").length < 40) return; // noise
      chunks.push({ index: chunks.length, heading: sec.heading, text, hash: sha256(text), chars: text.length });
    };
    for (const p of sec.paragraphs) {
      const body = p.text;
      if (p.table) {
        // tables travel alone; allow up to 2.5x size before splitting by rows
        if (buf) { push(buf); buf = ""; }
        if (body.length <= size * 2.5) push(body);
        else {
          const rows = body.split("\n");
          const head = rows.slice(0, 2).join("\n");
          let part = head;
          for (const r of rows.slice(2)) {
            if ((part + "\n" + r).length > size * 2) { push(part); part = head + "\n" + r; } else part += "\n" + r;
          }
          push(part);
        }
        continue;
      }
      if ((buf + "\n\n" + body).length > size && buf) {
        push(buf);
        buf = buf.slice(-overlap).trimStart() + "\n\n" + body;
      } else buf = buf ? buf + "\n\n" + body : body;
      if (buf.length > size) {
        const parts = splitLong(buf, size, overlap);
        for (const part of parts.slice(0, -1)) push(part);
        buf = parts[parts.length - 1];
      }
    }
    if (buf) push(buf);
  }
  return chunks;
}
