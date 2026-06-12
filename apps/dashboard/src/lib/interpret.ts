import { ArtifactInfo } from '../api/client'

/**
 * Human-readable, one-line interpretation of a KIO artifact — what this step
 * actually concluded. Used (in bold) on every artifact card so intermediary
 * and final results are legible without reading raw JSON.
 */
export function interpretArtifact(artifact: ArtifactInfo): string {
  const d = artifact.artifact_data as Record<string, unknown>
  const kio = artifact.producer_kio.toLowerCase()
  const num = (k: string): number | undefined =>
    typeof d[k] === 'number' ? (d[k] as number) : undefined
  const str = (k: string): string | undefined =>
    typeof d[k] === 'string' && d[k] ? (d[k] as string) : undefined
  const arrLen = (k: string): number | undefined =>
    Array.isArray(d[k]) ? (d[k] as unknown[]).length : undefined

  switch (kio) {
    case 'kio1': {
      const seq = Array.isArray(d.kio_sequence)
        ? (d.kio_sequence as string[]).map((k) => k.toUpperCase()).join(' → ')
        : '—'
      return `Routed the request to pipeline: ${seq}.` +
        (str('reasoning') ? ` ${str('reasoning')}` : '')
    }
    case 'kio2': {
      const stages = arrLen('stages') ?? arrLen('pipeline')
      return `Planned a ${stages ?? '—'}-stage pipeline.` +
        (str('reasoning') ? ` ${str('reasoning')}` : '')
    }
    case 'kio3':
      return `Analyzed ${num('file_count') ?? '—'} file(s) and found ` +
        `${num('finding_count') ?? arrLen('findings') ?? 0} potential issue(s) to validate.`
    case 'kio4':
      return str('summary') ??
        `Generated ${num('test_count') ?? '—'} test(s) across ` +
          `${num('file_count') ?? '—'} file(s)` +
          (str('framework') ? ` for ${str('framework')}.` : '.')
    case 'kio5': {
      const n = num('bug_count') ?? arrLen('bugs') ?? 0
      return `Confirmed ${n} real bug(s).` + (str('summary') ? ` ${str('summary')}` : '')
    }
    case 'kio6': {
      const patches = num('patch_count') ?? arrLen('patches') ?? 0
      const bugs = arrLen('bugs_addressed')
      return str('summary') ??
        `Applied ${patches} patch(es)${bugs !== undefined ? ` addressing ${bugs} bug(s)` : ''}.`
    }
    case 'kio7': {
      const p = num('passed') ?? 0
      const t = num('total') ?? 0
      const cov = num('coverage_pct')
      return `Re-ran tests: ${p}/${t} passed` +
        (cov !== undefined ? `, ${cov}% coverage.` : '.') +
        (str('interpretation') ? ` ${str('interpretation')}` : '')
    }
    case 'kio8': {
      const report = (d.report as Record<string, unknown>) ?? {}
      const verdict = (report.verdict as string) ?? '—'
      const summary =
        (report.executive_summary as string) ?? (report.verdict_reason as string) ?? ''
      return `Final verdict: ${verdict}.` + (summary ? ` ${summary}` : '')
    }
    case 'kio9': {
      const len = typeof d.code === 'string' ? (d.code as string).length : 0
      return `Generated ${str('language') ?? 'source'} code (${len} characters).`
    }
    case 'kio10':
      return str('summary') ?? 'Produced deployment recommendations.'
    case 'kio11':
      return str('summary') ?? `Produced ${arrLen('files') ?? '—'} file(s).`
    case 'kio12':
      return str('owasp_summary') ?? str('summary') ?? 'Completed the OWASP security scan.'
    case 'kio13':
      return str('summary') ?? 'Produced a module breakdown.'
    default:
      return str('summary') ?? str('message') ?? 'Result produced.'
  }
}
