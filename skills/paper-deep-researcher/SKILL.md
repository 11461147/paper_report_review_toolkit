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

## 執行流程

單篇論文必須使用「自動逐章調研」流程；多篇文獻回顧則使用下列四階段流程。

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

## 單篇論文：自動逐章調研與最終合成

單篇論文不得直接從全文生成總結，必須依序完成：

```text
PDF 與 metadata
  → 自動章節解析
  → 每章獨立 fresh context 撰寫
  → 每章 L1/L2 audit
  → verified claim registry
  → 全新 context 進行跨章 synthesis
  → L3a/L3b 與 final review
```

### 1. 自動解析 PDF 章節

- 直接讀取 PDF；需要純文字時自動轉換，並保留頁碼、行號與版面線索。
- 依序使用 PDF bookmarks/目錄、編號序列、排版特徵、常見學術章節名與 heading patterns 判定邊界與層級；過濾頁眉頁腳、圖說、表格列與 References 內的偽標題。
- 低信心候選由受限制的模型 job 自動判定；模型只能回傳 `is_heading`、`number`、`title`、`level` 與 `confidence`，不得改寫內文或創造章節。
- 小節不可靠時自動退回可靠的主章層級；連主章都無法可靠辨識時，以 `section_extraction_failed` 停止 pipeline。不得靜默猜測，也不得要求使用者人工修正。
- 輸出 `sections.json`；每章至少包含 section ID、number、title、level、text、start/end line、page range、confidence 與 detection source。

### 2. 每章使用獨立 fresh context

- 每個主要章節必須是獨立無狀態 job；章節之間以 JSON artifact 傳遞，不依賴前一個模型的對話歷史。
- Writer 只可取得論文 metadata、當前章節原文、固定 schema，以及必要且已驗證的前置章節摘要。
- 不得引用當前章未出現的結果，不得提前形成整篇論文結論。
- 每章輸出至少包含：章節目的、依原文順序的解讀、claims、evidence passages 與位置、公式/數字/圖表、本章明示限制、與前後章關係、僅限本章證據的小結。

### 3. 逐章 report 與 audit

- 先用 L1 deterministic checks 驗證 section ID、evidence 原文存在性、數字、引用位置、必填欄位與跨章污染。
- 再以獨立 reviewer fresh context 進行 L2 semantic support review；Reviewer 只看 claim、最小必要 evidence/context、位置與 L1 結果。
- 未通過章節必須在新 context 中重寫並重審；其 claims 不得進入後續合成。
- 每章 factual claim 必須可追溯至當章原文位置；無直接證據時明確標記 `No direct evidence found in evaluated literature.`

### 4. Verified claim registry

- 僅合併章節 audit 通過的 claims，並保留 `claim_id`、`section_id`、claim text、evidence text/location、citation ID、scope、verification status 與 contradictions。
- Registry 是最終結論唯一允許使用的事實來源；不得在 synthesis 階段從原文或記憶新增未審核主張。
- 衝突證據必須保留，不得為了產生單一結論而刪除。

### 5. 最後 synthesis 與 L3

- 所有必要章節通過後，才能在全新 synthesis context 撰寫跨章結論。
- Synthesis 只取得已驗證章節摘要、claim registry、數字/圖表 records、限制與衝突，不重新自由閱讀整篇 PDF。
- 每個跨章結論必須列出 supporting claim IDs，區分直接證據與 reviewer inference，並保留 dataset、task、method、metric、baseline 與實驗條件等 scope。
- L3a 先正規化 premises/conclusion/scope/inference rule；L3b 再審查推論有效性。L3 只處理跨章合成、比較、因果、泛化與實務建議，不審查單純方法轉述、公式定義、直接表格數字或 References。
- L1 分數不得由模型改寫；任一 L2/L3 blocking verdict 都必須阻止 final review，直到改寫、補證據或刪除主張後重跑。
- 最終 Markdown 先保留依原論文章節撰寫的逐章報告，然後在文末依序加入：`📌 一句話總結`、`🧭 論文定位與研究問題`、`📖 詳細原理解說`、`🎨 ASCII 圖解`、`📐 數學公式`、`⚙️ 實作細節`、`🧪 實驗設計與結果`、`✅ 優缺點分析`、`💡 適用場景建議`、`限制、風險與未來方向`、`📚 參考來源`。
- 文末綜合章節仍只能使用 verified claim registry；完成後必須再次執行 L1、L2、L3 與 final review，不得因逐章報告已通過而略過最終審查。
- 在 L2/L3 前執行兩階段 open-world comprehension audit：先讓全新 context 只讀完整報告，動態找出理解任何重要主張仍需回答的問題，不得依賴預設術語名單；再為每個缺口建立只含首次出現、局部上下文與候選原文 passages 的隔離 resolution job。
- Resolution 不得用模型常識補定義；答案必須來自報告既有文字或可逐字驗證的來源 passage。修正文稿後必須再由全新 context 完整閱讀最終報告，任何未回答的符號意義、縮寫、數值關係、baseline、metric、dataset、方法角色或 scope 都是 blocking finding。
- 校準 comprehension blocking：只有缺漏會實質改變主張意義、數值關係、比較或明示 scope 時才阻擋；不得要求理解該句所不需要的完整背景、資料集 provenance、普通詞定義或實作細節，也不得在最低必要定義已補齊後遞迴創造新問題。額外深度只能標 warning。
- 不得把 prompt、packet、schema、mock 或人工代跑當成模型能力驗證。宣稱工具通過前，必須從使用者實際 CLI/API 入口呼叫真實 backend 跑正反 golden cases，分別驗證可修正案例能 closure、無來源定義案例會 fail closed，並保存 run artifacts。

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
- [ ] 若使用 report_auditor，先以 L1 deterministic audit 檢查證據與格式，再用 L2 semantic packet 審 citation 是否支撐 claim，先用 L3a argument packet 輸出結構化 premises/conclusion/scope/inference rule，再用 L3b reasoning packet 審前提到結論的推論有效性；模型不得自行重算 L1 分數。

## 報告後審查流程

完成報告後，建議使用分層審查：

```text
L1 deterministic evidence checks
  用 code 檢查 citation、URL、DOI/arXiv、數字、章節、格式、metadata 與 hard gates。

L2 semantic support review
  模型只判斷 citation/context 是否真的支撐 claim，不重算分數。

L3a argument normalization
  模型先把自然語言 claim 拆成 premises、conclusion、scope conditions、inference rule、implicit assumptions 與 missing premises；不判斷前提真偽。

L3b inference validity review
  模型只判斷已驗證前提能否推出結論，檢查 overgeneralization、causal jump、
  missing condition、scope creep。
```

L3 推論審查應放在正文後面的附錄，不要混入主要報告敘事：

```md
## 推論有效性審查附錄

| ID | 原結論 | 已驗證前提 | 推論檢查 | 限制條件 | Verdict | 問題 | 安全結論 |
|---|---|---|---|---|---|---|---|
```

附錄只能審正文既有 claim 與 L1/L2 提供的 evidence/context，不得替正文補新證據。若 verdict 不是 `valid`，安全結論必須縮小範圍、保留限制條件，或要求補證據。

正式閉環審查必須把模型 verdict 回灌 auditor：

```powershell
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit_closed.json --semantic-verdicts report.semantic_verdicts.json --reasoning-verdicts report.reasoning_verdicts.json --final-review-out report.final_review.json --require-model-reviews
```

L2/L3 verdict 不得覆寫 L1 分數；但若出現 `unsupported`、`unverifiable`、`overgeneralized`、`causal_jump`、`missing_condition`、`unclear` 等 blocking verdict，最終報告不得標示為通過，必須先改寫、補證據或刪除 claim 後重跑。

可接受結論的條件：

```text
verified source evidence
+ complete premises
+ valid inference
+ preserved scope conditions
= conclusion acceptable within evidence scope
```

## Bundled Resources

- `references/report-template.md`：長篇文獻調研報告模板。
- `references/tools-schema.json`：原 Gemini plugin 的 function schema，可作為 MCP/tool adaptation 參考。
- `scripts/paper_research_skill.py`：Semantic Scholar / arXiv helper，可在需要 API-based search 時讀取或改造。
