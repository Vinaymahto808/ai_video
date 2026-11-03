import { test } from "node:test";
import assert from "node:assert/strict";
import { classifyPageType, isAllowedUrl, normalizeUrl } from "../src/crawler/urls.js";
import { websites } from "../src/config.js";

const acr = websites.auto_car_repair;

test("normalizeUrl strips fragments, tracking params, trailing slash, default ports", () => {
  assert.equal(normalizeUrl("https://AutoCarRepair.in:443/services/?utm_source=x&b=2&a=1#top"), "https://autocarrepair.in/services?a=1&b=2");
  assert.equal(normalizeUrl("https://autocarrepair.in/"), "https://autocarrepair.in/");
  assert.equal(normalizeUrl("/faq/", "https://autocarrepair.in/x"), "https://autocarrepair.in/faq");
  assert.equal(normalizeUrl("https://autocarrepair.in//blog//index.html"), "https://autocarrepair.in/blog");
  assert.equal(normalizeUrl("mailto:a@b.c"), null);
  assert.equal(normalizeUrl("javascript:void(0)"), null);
});

test("duplicate URLs collapse to one key", () => {
  const a = normalizeUrl("https://autocarrepair.in/faq?fbclid=1");
  const b = normalizeUrl("https://autocarrepair.in/faq/#q3");
  assert.equal(a, b);
});

test("isAllowedUrl enforces host and disallow patterns", () => {
  assert.equal(isAllowedUrl("https://autocarrepair.in/services", acr), true);
  assert.equal(isAllowedUrl("https://www.autocarrepair.in/faq", acr), true);
  assert.equal(isAllowedUrl("https://galaxytoyota.in/", acr), false);
  assert.equal(isAllowedUrl("https://autocarrepair.in/admin", acr), false);
  assert.equal(isAllowedUrl("https://autocarrepair.in/cart", acr), false);
  assert.equal(isAllowedUrl("https://autocarrepair.in/checkout/step-1", acr), false);
  assert.equal(isAllowedUrl("https://autocarrepair.in/images/logo.png", acr), false);
  assert.equal(isAllowedUrl("https://autocarrepair.in/blog/?s=brake", acr), false);
  assert.equal(isAllowedUrl("https://autocarrepair.in/profile", acr), false);
  assert.equal(isAllowedUrl("not a url", acr), false);
});

test("classifyPageType", () => {
  assert.equal(classifyPageType("https://autocarrepair.in/"), "home");
  assert.equal(classifyPageType("https://autocarrepair.in/faq"), "faq");
  assert.equal(classifyPageType("https://autocarrepair.in/service-centers"), "location");
  assert.equal(classifyPageType("https://autocarrepair.in/car-service-in-gurgaon"), "location");
  assert.equal(classifyPageType("https://autocarrepair.in/blog/brake-pads"), "blog");
  assert.equal(classifyPageType("https://autocarrepair.in/offers"), "offers");
  assert.equal(classifyPageType("https://autocarrepair.in/car-ac-repair"), "service");
  assert.equal(classifyPageType("https://autocarrepair.in/about"), "about");
});
