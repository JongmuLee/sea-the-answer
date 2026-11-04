const text = (value, limit, allowEmpty = false) =>
  typeof value === 'string' && value.length <= limit && (allowEmpty || value.trim().length > 0);

function validCount(value, key, count) {
  if (value.truncated === undefined && value[key] === undefined) return true;
  return typeof value.truncated === 'boolean' && Number.isSafeInteger(value[key]) &&
    value[key] >= count && value.truncated === (value[key] > count);
}

// History can outlive a response contract. Reject malformed presentation data as
// one unit and let the caller keep showing the answer's original text/citations.
export function validatedPresentation(value, evidence = []) {
  if (!value || typeof value !== 'object' || !text(value.title, 200)) return null;
  if (value.type === 'table') {
    if (!['documents', 'operations'].includes(value.source) ||
        !Array.isArray(value.columns) || value.columns.length < 1 || value.columns.length > 8 ||
        !value.columns.every(column => text(column, 80)) ||
        !Array.isArray(value.rows) || value.rows.length < 1 || value.rows.length > 50 ||
        !validCount(value, 'totalRows', value.rows.length)) return null;
    const sources = new Map((Array.isArray(evidence) ? evidence : [])
      .filter(item => item && typeof item.id === 'string').map(item => [item.id, item]));
    for (const row of value.rows) {
      if (!row || !Array.isArray(row.cells) || row.cells.length !== value.columns.length ||
          !row.cells.every(cell => text(cell, 1000, true)) || !Array.isArray(row.citations)) return null;
      if (value.source === 'operations') {
        if (row.citations.length !== 0) return null;
      } else {
        if (row.citations.length < 1 || row.citations.length > 5) return null;
        for (const citation of row.citations) {
          const source = citation && sources.get(citation.chunkId);
          if (!source || !text(source.document_id, 1000) || typeof source.text !== 'string' ||
              !text(citation.quote, 1000) || citation.quote.trim().length < 8 ||
              !source.text.includes(citation.quote)) return null;
        }
      }
    }
    return value;
  }
  if (value.type === 'chart') {
    if (value.source !== 'operations' || !['line', 'bar'].includes(value.chartType) ||
        !text(value.xLabel, 200) || !text(value.yLabel, 200) || !text(value.unit, 40, true) ||
        !Array.isArray(value.points) || value.points.length < 1 || value.points.length > 100 ||
        !validCount(value, 'totalPoints', value.points.length) ||
        !value.points.every(point => point && text(point.label, 80) &&
          typeof point.value === 'number' && Number.isFinite(point.value) && point.value >= 0)) return null;
    return value;
  }
  return null;
}

export function chartCoordinates(points, chartType) {
  const width = 760, height = 320, left = 88, right = 24, top = 24, bottom = 74;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const maximum = Math.max(...points.map(point => point.value));
  // Zero-only and one-point series still have a useful, finite plot domain.
  const domain = maximum > 0 ? maximum : 1;
  const scaled = points.map((point, index) => ({
    ...point,
    x: chartType === 'bar' ? left + (index + 0.5) * plotWidth / points.length
      : points.length === 1 ? left + plotWidth / 2 : left + index * plotWidth / (points.length - 1),
    y: top + plotHeight * (1 - point.value / domain),
  }));
  return { width, height, left, top, plotWidth, plotHeight, baseline: top + plotHeight,
    barWidth: Math.min(48, plotWidth / points.length * 0.7), points: scaled,
    ticks: [0, 0.25, 0.5, 0.75, 1].map(part => ({ value: domain * part,
      y: top + plotHeight * (1 - part) })) };
}
