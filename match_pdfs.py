"""Match PDF filenames against reference titles from titles.txt."""
import re
import os
import sys
from difflib import SequenceMatcher

sys.stdout.reconfigure(encoding='utf-8')


def normalize(s):
    s = s.lower()
    s = re.sub(r'[·．\.\s\-_\(\)\[\],:;]+', '', s)
    return s


def extract_key_tokens(title):
    t = title.strip()
    tokens = []
    for tok in re.split(r'[·．\.\s\-_—/]+', t):
        tok = tok.strip('[]()（）')
        if len(tok) >= 3:
            tokens.append(tok)
    return set(normalize(t) for t in tokens)


def main():
    with open('titles.txt', 'r', encoding='utf-8') as f:
        ref_lines = [l.strip() for l in f if l.strip()]

    refs = []
    for line in ref_lines:
        line_clean = re.sub(r'^\[\d+\]\s*', '', line)
        refs.append(line_clean)

    pdf_dir = r'pdfs\交际翻译参考文献'
    pdf_files = [f for f in os.listdir(pdf_dir) if f.lower().endswith('.pdf')]

    matched_pdfs = set()
    matched_refs = []
    pairs = []

    for pdf in pdf_files:
        pdf_norm = normalize(pdf)
        pdf_tokens = extract_key_tokens(pdf.replace('.pdf', ''))

        best_score = 0
        best_ref = ''
        for ref in refs:
            ref_norm = normalize(ref)
            ref_tokens = extract_key_tokens(ref)

            overlap = pdf_tokens & ref_tokens
            union = pdf_tokens | ref_tokens
            token_score = len(overlap) / len(union) if union else 0

            seq_score = SequenceMatcher(None, pdf_norm[:100], ref_norm[:100]).ratio()
            combined = token_score * 0.6 + seq_score * 0.4

            if combined > best_score:
                best_score = combined
                best_ref = ref

        if best_score >= 0.15:
            pairs.append((pdf, best_ref, best_score))
            matched_pdfs.add(pdf)
            matched_refs.append(best_ref)

    pairs.sort(key=lambda x: -x[2])

    print('=== 已匹配 %d 篇 ===' % len(pairs))
    for pdf, ref, score in pairs:
        ref_short = ref[:70]
        print(f'  [{score:.2f}] {pdf[:55]}...')
        print(f'          -> {ref_short}')

    unmatched_refs = [r for r in refs if r not in matched_refs]
    unmatched_pdfs = [p for p in pdf_files if p not in matched_pdfs]

    print()
    print(f'匹配: {len(pairs)}  缺文献: {len(unmatched_refs)}  多余PDF: {len(unmatched_pdfs)}')
    print()

    if unmatched_refs:
        print('=== 缺失文献 ===')
        for r in unmatched_refs:
            print(f'  X {r[:80]}')

    if unmatched_pdfs:
        print()
        print('=== 多余PDF（论文不引用） ===')
        for p in unmatched_pdfs:
            print(f'  - {p[:80]}')


if __name__ == '__main__':
    main()
