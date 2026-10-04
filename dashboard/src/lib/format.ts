/** Presentation formatting never changes the API's coverage or missing-data semantics. */
const numbers = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
export function number(value: number | null | undefined): string {
  return value == null ? '—' : numbers.format(value);
}
export function milliseconds(value: number | null | undefined): string {
  return value == null ? '—' : `${number(value)} ms`;
}
export function percent(value: number | null | undefined): string {
  return value == null ? 'Unknown' : `${number(value * 100)}%`;
}
export function timestamp(value: number | null | undefined): string {
  return value == null ? '—' : new Date(value * 1000).toLocaleString();
}
export function duration(seconds: number): string {
  return seconds >= 3600
    ? `${number(seconds / 3600)}h`
    : seconds >= 60
      ? `${number(seconds / 60)}m`
      : `${number(seconds)}s`;
}
export function exportJson(name: string, data: unknown): void {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
  );
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `mas-${name}.json`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
