# Citation Quality Rules

Adapted from academic writing quality principles. Applied post-generation.

## 1. Structural Honesty

A generated citation must NOT assert a claim that is not verifiable from the reference's abstract, key arguments, or methods.

**Red flags (flag, don't auto-fix):**
- The citation attributes a specific finding to the reference that doesn't appear in its abstract
- The citation says "X proposed Y" when the reference only discusses Y without attribution to X
- Quantitative claims ("reduced by 30%") that don't appear in the source material

**When flagged:** append `[需核实]` marker and reduce confidence.

## 2. Claim-Evidence Link

Every citation must connect:
- A **specific claim** from the thesis paragraph
- To a **specific piece of evidence** from the reference (a finding, argument, method, or theoretical framework)

**Weak link patterns (avoid):**
- "X也研究了Y" — says nothing about what X found or why it matters
- "这与X的研究一致" — doesn't specify what is consistent or how
- Namedrop citations: mentioning a reference without engaging with its content

**Strong link pattern:**
- "X通过对Y的分析发现Z，为本文的W观点提供了实证支持"

## 3. AI-Trace Removal

Common Chinese AI writing patterns to detect and rewrite:

| Pattern | Why it's AI-like | Alternative |
|---------|-----------------|-------------|
| "在当今时代/社会" | Vague temporal anchoring | Delete or use specific context |
| "众所周知/毋庸置疑" | Filler that adds no information | Delete |
| "具有重要意义/深远影响" | Vague value judgment | State specific significance |
| "提供了宝贵的参考/借鉴" | Generic praise | Say what specifically is useful |
| "值得注意的是/值得强调的是" | Overused transition | Use "具体而言/其中" or restructure |
| "不仅...而且..." | Overused parallel structure | Use simpler structure or split sentences |
| "综上所述/总而言之" at start | Mid-paragraph summary signal | Only use at section end |

## 4. Academic Register

- Default: formal written Chinese (书面语), not spoken register
- No first-person pronouns (我/我们) unless the thesis itself uses them
- No rhetorical questions
- No exclamation marks
- Prefer specific verbs: "指出/论证/揭示/验证" over vague "研究/探讨/分析"

## 5. Length & Density

- 1-3 sentences, 40-120 Chinese characters
- One key point per sentence
- If the reference supports multiple distinct claims, split into separate citations rather than one dense paragraph

## 6. Citation Type Guidance

- **supporting**: "X的研究印证了这一观点..." / "X对Y的分析表明..."
- **contrasting**: "与...不同，X指出..." / "X则从Z角度提出了不同看法..."
- **foundational**: "X为Y领域奠定了理论基础..." / "X提出的Z框架..."
- **methodological**: "本文借鉴了X在Y研究中使用的Z方法..." / "X的Z方法为本文的...提供了操作路径"

## Validation Workflow

After generation, check each citation against these rules:

1. Can every factual claim in the citation be traced to the reference's abstract/arguments/methods? If not → flag.
2. Does the citation name a specific connection between the thesis and the reference? If not → rewrite.
3. Does the citation contain any AI-trace patterns? If yes → rewrite.
4. Is the register appropriately formal? If not → rewrite.
5. Is the length within 40-120 characters? If not → adjust.
