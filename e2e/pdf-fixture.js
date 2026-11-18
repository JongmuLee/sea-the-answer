// ASCII-only synthetic PDF pages. An empty string produces a blank page.
export function pdfFixture(...pages) {
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    `<< /Type /Pages /Kids [${pages.map((_, i) => (4 + i * 2) + ' 0 R').join(' ')}] /Count ${pages.length} >>`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  for (const text of pages) {
    const content = text ? `BT /F1 12 Tf 50 750 Td (${text}) Tj ET` : '';
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents ${objects.length + 2} 0 R >>`,
      `<< /Length ${content.length} >>\nstream\n${content}\nendstream`);
  }
  let pdf = '%PDF-1.4\n';
  const offsets = [];
  objects.forEach((obj, i) => {
    offsets.push(pdf.length);
    pdf += `${i + 1} 0 obj\n${obj}\nendobj\n`;
  });
  const count = objects.length + 1, xref = pdf.length;
  return pdf + `xref\n0 ${count}\n0000000000 65535 f \n` +
    offsets.map(n => String(n).padStart(10, '0') + ' 00000 n ').join('\n') +
    `\ntrailer\n<< /Size ${count} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
}
