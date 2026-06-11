import { build } from 'esbuild';
import { readFile } from 'node:fs/promises';

const check = process.argv.includes('--check');

const bundles = [
  {
    entry: 'src/personal_db/ui/apps/finance/burn-rate.jsx',
    outfile: 'src/personal_db/ui/static/apps/finance-burn-rate.js',
    globalName: 'pdbFinanceBurnRateBundle',
  },
  {
    entry: 'src/personal_db/ui/apps/finance/categorize.jsx',
    outfile: 'src/personal_db/ui/static/apps/finance-categorize.js',
    globalName: 'pdbFinanceCategorizeBundle',
  },
  {
    entry: 'src/personal_db/ui/apps/finance/rules.jsx',
    outfile: 'src/personal_db/ui/static/apps/finance-rules.js',
    globalName: 'pdbFinanceRulesBundle',
  },
];

function optionsFor(bundle, overrides = {}) {
  return {
    entryPoints: [bundle.entry],
    outfile: bundle.outfile,
    bundle: true,
    format: 'iife',
    globalName: bundle.globalName,
    jsx: 'automatic',
    minify: true,
    sourcemap: false,
    target: ['es2020'],
    logLevel: 'info',
    ...overrides,
  };
}

for (const bundle of bundles) {
  if (!check) {
    await build(optionsFor(bundle));
  } else {
    const result = await build(optionsFor(bundle, { write: false, logLevel: 'silent' }));
    const next = Buffer.from(result.outputFiles[0].contents).toString('utf8');
    let current = '';
    try {
      current = await readFile(bundle.outfile, 'utf8');
    } catch (_error) {
      // Missing bundle is reported below as a mismatch.
    }
    if (next !== current) {
      console.error(`${bundle.outfile} is out of date; run npm run build:ui`);
      process.exitCode = 1;
    }
  }
}
