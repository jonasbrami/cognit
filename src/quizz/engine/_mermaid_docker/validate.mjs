// Parse-only mermaid validator. Reads a mermaid source from stdin, calls
// mermaid.parse(), exits 0 on success and 1 on parse error. The error message
// (if any) goes to stderr so the caller can surface it.
//
// We set up jsdom before importing mermaid because mermaid touches `document`
// at module-load time even in pure-parse mode. The HTML doc is empty — we
// never render, just parse.

import { JSDOM } from "jsdom";

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
  pretendToBeVisual: true,
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.navigator = dom.window.navigator;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.Node = dom.window.Node;

const mermaid = (await import("mermaid")).default;

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const src = Buffer.concat(chunks).toString("utf-8");

try {
  await mermaid.parse(src);
  process.exit(0);
} catch (e) {
  process.stderr.write(String(e && e.message ? e.message : e) + "\n");
  process.exit(1);
}
