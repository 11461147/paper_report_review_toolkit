---
name: paper-deep-researcher
description: 嚴謹的學術論文深度調研與證據合成 skill，適用於文獻回顧、survey、論文比較、研究缺口分析、技術報告、引用驗證、citation-grounded academic synthesis。當使用者要求查找、整理、比較、總結或分析學術論文，尤其需要每個主張都有可靠 citation 支撐時使用。
---

# Paper Deep Researcher

使用這個 skill 時，先做證據蒐集，再做結論合成。目標是零幻覺、完整引用、可追溯來源的學術調研流程。

優先使用 peer-reviewed papers、官方 preprint、arXiv、Semantic Scholar、OpenAlex、Crossref、DOI、publisher page、會議論文頁、官方 PDF。網路搜尋摘要只能當線索，不可直接當 citation 來源。

## 核心調研規則

1. 不得產生沒有明確論文 citation 的事實主張。
2. 每個方法細節、benchmark 數字、實驗結果、比較結論，都必須附上 citation。
3. Citation 優先使用格式：`[Author et al., Year, S2ID:paperId]`。
4. 複雜問題必須先拆成至少 3 個 boolean-style sub-queries，但 query matrix 只作為內部規劃，不要放進最終報告，除非使用者明確要求。
5. 搜尋時要結合 lexical keyword search 與 semantic/related-paper search；若沒有向量工具，至少用多組關鍵字、同義詞、縮寫、引用網路補足。
6. 證據片段 relevance score `R(c|q) < 0.75` 時不得用於最終合成。
7. 必須主動找出並記錄不同論文之間的矛盾、條件差異與可能原因。
8. 不得信任 web search summaries 作為 citation source；必須閱讀或驗證原始 URL、abstract page、PDF、DOI page 或 publisher page。
9. 找不到直接證據時，明確寫：`No direct evidence found in evaluated literature.`
10. 寧可少寫，也不要寫錯；better empty than wrong。

## 觸發情境

當使用者提出以下類型請求時使用本 skill：

- 「幫我調研某個主題的論文」
- 「整理某領域文獻回顧」
- 「比較論文 A 和論文 B」
- 「找某技術的 survey papers」
- 「分析研究缺口 / open challenges」
- `literature review on ...`
- `survey papers about ...`
- `find academic papers on ...`
- `compare these papers ...`

## 四階段執行流程

### Phase 1: Query Planning and Expansion

先分析使用者意圖，建立內部 query matrix。這是調研規劃，不是報告內容。最終報告不要輸出 Query Matrix，除非使用者明確要求。

內部規劃至少包含：

- 主題核心名詞。
- 同義詞、縮寫、別名。
- 方法名稱、benchmark 名稱、dataset 名稱。
- 至少 3 個 boolean-style sub-queries。
- 可能的 upstream foundational papers。
- 可能的 downstream recent papers。

### Phase 2: Literature Search and Graph Navigation

依可用工具選擇最佳搜尋路線：

1. 若有學術搜尋 MCP 或專用工具，優先使用 `search_academic_papers`、Semantic Scholar、OpenAlex、Crossref、arXiv、Zotero 等。
2. 若有 citation graph 工具，對核心論文執行 references 與 citations 雙向擴展。
3. 若無專用工具，使用網路搜尋，但只把搜尋結果當 discovery hints，最終仍要打開原始 scholarly source 驗證。
4. 若可使用 bundled script，可讀取或改用 `scripts/paper_research_skill.py`。

搜尋時要：

- 對每個 boolean query 搜尋。
- 對 top seed papers 追蹤 references、citations、recommendations。
- 優先保留高相關、高引用、近期、重要 venue、直接回答問題的論文。
- 使用 composite impact intuition 排序：citation count、influential citations、publication recency、venue relevance、topic directness。

### Phase 3: Evidence Extraction and Fact Scoring

對每篇候選論文：

1. 驗證 metadata：title、authors、year、venue、S2ID、DOI、arXiv ID、canonical URL。
2. 讀取 abstract、introduction、method、experiment、results、limitations、conclusion 中與 sub-question 直接相關的段落。
3. 為每個 evidence snippet 估計 `R(c|q)`：
   - `R >= 0.90`：Primary evidence，可直接作為主要證據。
   - `0.75 <= R < 0.90`：Supporting evidence，可作為輔助證據。
   - `R < 0.75`：Discard，不得用於結論。
4. 每個保留片段都要標記來源：`[Author et al., Year, S2ID:paperId]`。
5. Benchmark 數字、dataset 名稱、公式、方法限制，只能使用來源中明確出現的內容。

### Phase 4: Structured Synthesis

最終報告預設使用中文。除非使用者要求精簡，單篇論文調研報告應包含：

- 摘要與論文定位。
- 📖 詳細原理解說（從零開始）。
- 🎨 ASCII 圖解。
- 📐 數學公式 + 白話翻譯。
- ⚙️ 實作細節。
- 實驗設計與結果分析。
- ✅ 優缺點分析。
- 💡 適用場景建議。
- 限制、風險與未來方向。
- References / 已驗證參考文獻。

報告不要輸出 Query Matrix。內部搜尋規劃與思考過程不應污染最終報告。

## 工具使用規則

| Tool | 使用時機 |
|:--|:--|
| `search_academic_papers` | 初始查詢、關鍵字探索、候選論文發現 |
| `traverse_citation_graph` | 從核心論文擴展 references / citations / recommendations |
| `get_paper_snippets` | 從論文抽取精確 evidence passages |
| `get_paper_metadata` | 驗證 title、authors、year、venue、abstract、external IDs |
| `read_url_content` | 引用前驗證 canonical URL、title、authors、abstract；若可用則必須使用 |
| web search | 只能作為 discovery hints；最終 citation 必須回到原始學術來源 |

如果上述工具不可用，使用目前環境中最接近的官方來源搜尋方式，並在回答中說明限制。

## Citation URL Verification Protocol

這是強制規則。Citation URL 不可只因為看起來合理就使用。

### Rule 1: Never Skip URL Verification

在 References 中加入任何 URL 前，必須驗證：

- Title 是否一致。
- Authors 是否一致。
- Year 是否一致。
- Abstract 或 paper page 是否與主題一致。
- DOI / arXiv ID / S2ID 是否對得上。

若 `read_url_content` 可用，引用前必須讀取 URL。若工具不可用，至少打開原始頁、publisher page、DOI page、arXiv page 或 Semantic Scholar page 做人工式驗證。

### Rule 2: 不要誤用 arXiv ID / DOI / IEEE Doc ID

ID 必須來自原始頁面或可靠 metadata。不要把搜尋摘要、URL 片段、或看似相關的 IEEE/DOI/arXiv ID 直接配到另一篇論文。

特別注意：

- arXiv ID 只能對應 arXiv 原始頁或官方 metadata。
- DOI 必須能解析到相同 title 和 authors。
- IEEE / ACM / Springer 等頁面 ID 不可只靠搜尋結果推測。

### Rule 3: Cross-check by Author + Year + Title Keywords

Citation 至少要交叉檢查三個條件：

```text
Author last name  必須匹配
Publication year  必須匹配
Title keywords    必須匹配
```

任一條件不符時，不得當作 verified citation。必要時標記：`UNVERIFIED - requires manual confirmation`。

### Rule 4: Better Empty Than Wrong

如果 URL 或 metadata 不能驗證：

- 不要硬塞進 References。
- 不要把錯誤 title 配給正確 ID。
- 不要把正確 title 配給錯誤 URL。
- 寫明 `No verified citation available from evaluated sources.`

### Rule 5: Resist Plausibility Bias

看起來合理不代表正確。特別是相似標題、相同作者群、同一年份、多版本 preprint、survey 引用、搜尋引擎摘要，都容易造成錯配。

必須用 metadata 驗證，而不是用直覺判斷。

## 反幻覺 Checklist

最終輸出前檢查：

- [ ] 每個 factual claim 都有 citation。
- [ ] 每個 benchmark number 都能回到原始 paper evidence。
- [ ] 每個 citation 都驗證過 title、authors、year、URL。
- [ ] 沒有把 web search summary 當 citation source。
- [ ] `R(c|q) < 0.75` 的片段沒有進入結論。
- [ ] 找不到證據的 sub-question 有明確寫 `No direct evidence found in evaluated literature.`
- [ ] 已標出 contradictions、limitations、open challenges。
- [ ] References 已去重。
- [ ] 無法驗證的來源已標為 `UNVERIFIED` 或省略。

## Bundled Resources

- `references/report-template.md`：長篇文獻調研報告模板。
- `references/tools-schema.json`：原 Gemini plugin 的 function schema，可作為 MCP/tool adaptation 參考。
- `scripts/paper_research_skill.py`：Semantic Scholar / arXiv helper，可在需要 API-based search 時讀取或改造。