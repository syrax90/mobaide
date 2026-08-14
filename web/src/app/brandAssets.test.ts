// @vitest-environment node
//
// Node, not jsdom: this reads the shipped `web/public/` assets, `index.html` and
// the marketing site's originals straight off disk with `node:fs`.
//
// The SPA and the site deploy from separate pipelines (deploy_web.py vs
// deploy_central.py) and share no asset build, so `web/public/` holds hand-made
// copies. Nothing else fails when one side is regenerated alone: a 404 icon
// still parses, and an <img> with the right `alt` still passes the e2e
// accessible-name gate.
/// <reference types="node" />
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/** `web/src/app/` → repo root. */
const REPO_ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const WEB_ROOT = join(REPO_ROOT, 'web');
const PUBLIC_DIR = join(WEB_ROOT, 'public');
const SITE_DIR = join(REPO_ROOT, 'central_server', 'static', 'site');

/**
 * `[web/public/…, central_server/static/site/…]`. Every file the two origins are
 * meant to serve identically, not just the ones this change introduced: the font
 * and its licence were already hand-copied before the icons joined them, and a
 * mirror nobody checks is the one that drifts. The site's regeneration runbook
 * (docs/website.md) points back here for the full set.
 */
const MIRRORED_ASSETS: readonly (readonly [string, string])[] = [
  ['favicon.ico', 'img/favicon.ico'],
  ['apple-touch-icon.png', 'img/apple-touch-icon.png'],
  ['wordmark.png', 'img/wordmark.png'],
  ['fonts/DepartureMono-Regular.woff2', 'fonts/DepartureMono-Regular.woff2'],
  ['fonts/DepartureMono-OFL.txt', 'fonts/DepartureMono-OFL.txt'],
];
const SHELLS = ['RootLayout.tsx', 'AuthLayout.tsx'];
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

const indexHtml = readFileSync(join(WEB_ROOT, 'index.html'), 'utf8');
const marketingHtml = readFileSync(
  join(REPO_ROOT, 'central_server', 'templates', 'marketing_landing.html'),
  'utf8',
);
const manifest = JSON.parse(readFileSync(join(PUBLIC_DIR, 'manifest.webmanifest'), 'utf8')) as {
  icons?: { src: string; sizes: string; type: string }[];
};

function tags(source: string, name: string): string[] {
  return Array.from(source.matchAll(new RegExp(`<${name}\\b[^>]*>`, 'g')), (m) => m[0] ?? '');
}

function attribute(tag: string, name: string): string {
  return new RegExp(`\\b${name}="([^"]*)"`).exec(tag)?.[1] ?? '';
}

/** JSX numeric literal, e.g. `width={227}`. */
function jsxNumber(tag: string, name: string): number {
  return Number(new RegExp(`\\b${name}=\\{(\\d+)\\}`).exec(tag)?.[1] ?? NaN);
}

function iconLinks(html: string): string[] {
  return tags(html, 'link').filter((tag) =>
    ['icon', 'apple-touch-icon'].includes(attribute(tag, 'rel')),
  );
}

/** Basename of an icon URL — the site's `/static/site/img/x.png?v=…` and the
 *  SPA's `/x.png` name the same artwork through different origins. */
function assetName(url: string): string {
  return url.split('?')[0]?.split('/').pop() ?? '';
}

function publicPath(url: string): string {
  return join(PUBLIC_DIR, ...url.replace(/^\//, '').split('/'));
}

function sha256(path: string): string {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

function pngSize(path: string): { width: number; height: number } {
  const bytes = readFileSync(path);
  if (!bytes.subarray(0, 8).equals(PNG_SIGNATURE)) throw new Error(`${path} is not a PNG`);
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

describe('brand assets', () => {
  it('ships every icon index.html declares', () => {
    const hrefs = iconLinks(indexHtml).map((tag) => attribute(tag, 'href'));

    expect(hrefs.length, 'no icon links parsed out of index.html').toBeGreaterThan(0);
    for (const href of hrefs) {
      expect(href, 'icon URLs are root-relative, served straight out of public/').toMatch(/^\//);
      expect(existsSync(publicPath(href)), `${href} has no file in web/public/`).toBe(true);
    }
  });

  it('declares the same icon set as the marketing site', () => {
    const declaration = (html: string) =>
      iconLinks(html)
        .map((tag) => ({
          rel: attribute(tag, 'rel'),
          file: assetName(attribute(tag, 'href')),
          sizes: attribute(tag, 'sizes'),
        }))
        .sort((a, b) => a.rel.localeCompare(b.rel));

    expect(declaration(indexHtml)).toEqual(declaration(marketingHtml));
  });

  // Deliberately asserts no minimum pixel size. Chrome's published install
  // criteria ask for 192 + 512 while Chromium has historically gated on a 144px
  // constant, and this repo cannot settle which governs — an assertion picking
  // either number would pass forever without telling anyone whether the claim
  // behind it is true. What is checkable is that each icon exists, is a real
  // PNG, and is declared at its actual size; sizing the set for installability
  // is CLA-252.
  it('ships every manifest icon at the size it claims', () => {
    const icons = manifest.icons ?? [];
    expect(icons.length, 'a manifest with no icon is not installable').toBeGreaterThan(0);

    for (const icon of icons) {
      expect(existsSync(publicPath(icon.src)), `${icon.src} has no file in web/public/`).toBe(true);
      const { width, height } = pngSize(publicPath(icon.src));
      expect(icon.sizes, `${icon.src} is declared at the wrong size`).toBe(`${width}x${height}`);
      expect(icon.type).toBe('image/png');
    }
  });

  it('keeps every mirrored asset byte-identical to the marketing site', () => {
    for (const [publicRelative, siteRelative] of MIRRORED_ASSETS) {
      const mine = join(PUBLIC_DIR, ...publicRelative.split('/'));
      const theirs = join(SITE_DIR, ...siteRelative.split('/'));

      expect(existsSync(theirs), `${siteRelative} is gone from central_server`).toBe(true);
      expect(sha256(mine), `${publicRelative} has drifted from central_server`).toBe(sha256(theirs));
    }
  });

  it('sizes the header mark in both shells to the wordmark it loads', () => {
    for (const shell of SHELLS) {
      const source = readFileSync(join(WEB_ROOT, 'src', 'app', shell), 'utf8');
      const mark = tags(source, 'img').find((tag) => attribute(tag, 'className') === 'brand-mark');

      expect(mark, `${shell} renders no .brand-mark image`).toBeDefined();
      const src = attribute(mark ?? '', 'src');
      expect(existsSync(publicPath(src)), `${shell} loads ${src}, absent from web/public/`).toBe(
        true,
      );
      // Intrinsic size, not a layout choice: `.brand-mark` sets width in CSS and
      // leaves `height: auto`, so a regenerated asset at a different aspect
      // ratio would silently reserve the wrong box before it decodes.
      expect(pngSize(publicPath(src))).toEqual({
        width: jsxNumber(mark ?? '', 'width'),
        height: jsxNumber(mark ?? '', 'height'),
      });
    }
  });

  it('declares no SVG icon and ships none', () => {
    expect(existsSync(join(PUBLIC_DIR, 'favicon.svg'))).toBe(false);
    expect(indexHtml).not.toContain('image/svg+xml');

    const declared = [
      ...iconLinks(indexHtml).map((tag) => attribute(tag, 'href')),
      ...(manifest.icons ?? []).map((icon) => icon.src),
    ];
    expect(declared.filter((url) => assetName(url).endsWith('.svg'))).toEqual([]);
  });
});
