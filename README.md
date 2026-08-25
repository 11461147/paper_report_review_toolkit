# 論文報告撰寫與審核 Toolkit

這是一套可複製到其他專案的論文調研與報告審核工具。它的目標不是讓模型自由打分，而是把「報告是否可信」拆成可追蹤的流程：先用程式做可計算檢查，再把 citation 支撐與推論有效性整理成 packet 交給模型做受限審查，最後把 verdict 回灌成最終通過或阻擋。

適合用在：

- 單篇論文深度調研報告。
- 多篇文獻的 citation-grounded synthesis。
- 需要繁體中文、引用可追蹤、推論不能亂跳的研究筆記。
- 想把「模型分析」和「形式審查」分開管理的報告流程。

## 目錄結構

```text
paper_report_review_toolkit/
├── skills/
│   └── paper-deep-researcher/
│       ├── SKILL.md
│       ├── agents/
│       ├── references/
│       └── scripts/
└── tools/
    ├── report_auditor.py
    └── smoke_test_auditor.py
```

## 元件用途

- `skills/paper-deep-researcher/`：給 Codex 使用的論文深度調研 skill，規範搜尋、閱讀、引用、報告結構與審查習慣。
- `tools/report_auditor.py`：主要審核器，負責 deterministic checks、hard gates、packet generation、final review merge。
- `tools/smoke_test_auditor.py`：回歸測試，快速確認 hard gates、L2/L3 packet 與 final review gating 沒有壞掉。

## 工作方式總覽

這套工具採用四層審查。

```text
source paper / extracted text
        |
        v
research report.md
        |
        v
L1 deterministic audit
  - citation、URL、DOI/arXiv、數字、表格、章節、亂碼、reference、hard gates
        |
        v
L2 semantic support packet
  - 交給模型檢查 citation/context 是否真的支撐 claim
        |
        v
L3a argument normalization packet
  - 交給模型把自然語言 claim 拆成 premises、conclusion、scope、inference rule
        |
        v
L3b inference validity packet
  - 交給模型檢查前提是否能推出結論
        |
        v
final review merge
  - 把 L2/L3 verdict JSON 回灌，blocking verdict 會讓最終審查不通過
```

核心原則：

```text
verified source evidence
+ complete premises
+ valid inference
+ preserved scope conditions
= conclusion acceptable within evidence scope
```

L1 用 code 做，不交給模型猜。L2/L3 用模型做，但模型只能在 packet 給定的 claim、context、citation 線索中工作，不得補外部知識，也不得重算 L1 分數。

## 快速開始

建議在 Windows PowerShell 顯式設定 UTF-8：

```powershell
$env:PYTHONUTF8="1"
$env:PYTHONIOENCODING="utf-8"
```

最常用的一次性審查命令：

```powershell
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit.json --rewrite-packet --semantic-packet --argument-packet --reasoning-packet --reasoning-appendix
```

如果只想先看報告是否過 L1 deterministic audit：

```powershell
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit.json
```

如果離線執行，不想做 metadata/link 類網路檢查：

```powershell
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit.json --no-verify-citation-metadata
```

如果要讓 CI 或 script 在審查不通過時回傳非 0 exit code：

```powershell
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit.json --fail-on-audit-fail
```

## 輸入文件要求

`report.md` 是你要交付的研究報告。建議包含以下章節，工具會檢查必要章節覆蓋率：

```md
## 📌 一句話總結
## 🧭 論文定位與研究問題
## 📖 詳細原理解說
## 🎨 ASCII 圖解
## 📐 數學公式
## ⚙️ 實作細節
## 🧪 實驗設計與結果
## ✅ 優缺點分析
## 💡 適用場景建議
## 限制、風險與未來方向
## 📚 參考來源
```

`--source paper_extracted.md` 是從原論文 PDF、HTML 或其他來源抽出的原文。它不是必填，但強烈建議提供，因為 auditor 會用它檢查數字是否出現在來源文字中。

推薦 citation 形式：

```md
GraphRAG 使用 Leiden community detection 建立階層式社群 [Edge et al., 2024, 頁 5, Section 3.1.4]。
```

建議每個重要 claim 都在句尾放 citation。不要把多個 citation 擠成不可解析的格式，也不要只在段落最後放一個籠統來源。

## 常用輸出

執行 auditor 後，常見輸出如下：

- `*_audit.json`：完整審核結果、分數、counts、hard gates、findings、reasoning candidates。
- `*.audit_rewrite_contexts.json`：未通過時給 AI 重寫用的上下文與 rewrite hints。
- `*.semantic_audit_packet.json`：L2 語義支撐審查 packet。
- `*.argument_packet.json`：L3a 論證正規化 packet。
- `*.reasoning_audit_packet.json`：L3b 推論有效性審查 packet。
- `*.reasoning_appendix.md`：可貼回報告後段的推論有效性審查附錄骨架。
- `*.final_review.json`：合併 L1/L2/L3 verdict 後的最終閉環審查結果。

`.gitignore` 預設忽略這些 audit/packet/output 檔案，避免把研究過程產物誤推到公開 repo。

## L1 Deterministic Audit

L1 是 `report_auditor.py` 自己做的 deterministic checks。它會檢查：

- 報告長度、章節數、必要章節覆蓋率。
- 可審核 claim 數、有 citation 的 claim 數。
- citation support rate、unsupported claim rate。
- URL、DOI、arXiv ID、reference 重複。
- 表格格式、數字是否能在 `--source` 中找到。
- 亂碼、PowerShell 常見編碼問題、模糊強詞、未定義縮寫。
- high-risk inference words，例如把相關性寫成因果、把局部結果擴張成全域結論。

L1 的通過條件：

- `final_score >= 80`
- 沒有 `critical` finding
- hard gates 全部通過

## Hard Gates

hard gates 是直接阻擋條件，不讓空報告或低證據報告靠加權分數踩線通過。

目前門檻：

| Gate | 門檻 |
|---|---:|
| 報告長度 | 至少 `600` 字元 |
| 可審核主張 | 至少 `3` 條 |
| 有 citation 的主張 | 至少 `1` 條 |
| citation 支撐率 | 至少 `35%` |
| 必要章節覆蓋率 | 至少 `60%` |
| references | 至少 `1` 個可辨識文獻條目 |

這些值目前寫在 `tools/report_auditor.py` 的常數中：

```python
MIN_REPORT_CHARS = 600
MIN_CLAIMS = 3
MIN_CITED_CLAIMS = 1
MIN_CITATION_SUPPORT_RATE = 35.0
MIN_REQUIRED_SECTION_RATE = 60.0
```

## L2 Semantic Support Review

L2 不是讓模型重新打分，而是讓模型回答一個更窄的問題：

```text
這個 claim 是否真的被它旁邊的 citation/context 支撐？
```

產生 L2 packet：

```powershell
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit.json --semantic-packet
```

輸出範例：

```text
report.semantic_audit_packet.json
```

模型閱讀 L2 packet 後，應輸出 verdict JSON。可用格式之一：

```json
{
  "items": [
    {
      "id": "L2-001",
      "evidence_status": "supported",
      "inference_risk": "low",
      "reason": "citation 明確支撐 claim。",
      "action": "keep"
    }
  ]
}
```

會阻擋 final review 的 L2 條件：

- `evidence_status`: `unsupported`
- `evidence_status`: `unverifiable`
- `action`: `delete`
- `action`: `replace_source`
- `evidence_status`: `partially_supported` 且 `inference_risk`: `high`

## L3a Argument Normalization

L3a 是「先輸出結構化 argument」的步驟。它不判斷結論對不對，只把自然語言 claim 正規化成可審查的論證形式。

產生 L3a packet：

```powershell
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit.json --argument-packet
```

L3a 模型應輸出這類結構：

```json
{
  "items": [
    {
      "id": "ARG-001",
      "source_claim_id": "L3-001",
      "original_claim": "原 claim 原文。",
      "normalized_argument": {
        "premises": [
          {
            "id": "P1",
            "text": "可由 context/citation 抽出的前提。",
            "evidence_status": "pending_l2",
            "source": "citation 或 context 指標"
          }
        ],
        "conclusion": {
          "id": "C",
          "text": "claim 的結論命題。"
        },
        "scope_conditions": [
          "資料集、任務、方法、metric、baseline、研究設計、證據等級等限制。"
        ],
        "inference_rule": "scope_limited_conclusion",
        "implicit_assumptions": [],
        "missing_premises": []
      },
      "ready_for_formal_review": true,
      "normalization_notes": "繁體中文短註解。"
    }
  ]
}
```

L3a 的禁忌：

- 不得判斷前提真偽。
- 不得補外部知識。
- 不得輸出 `valid` / `invalid` verdict。
- 若前提尚未由 L2 驗證，標記 `pending_l2`。

## L3b Inference Validity Review

L3b 審查的是推論形式：

```text
如果 L1/L2 支撐的前提都成立，這些前提是否足以推出報告的結論？
```

產生 L3b packet 與附錄骨架：

```powershell
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit.json --reasoning-packet --reasoning-appendix
```

模型應輸出 verdict JSON。可用格式之一：

```json
{
  "items": [
    {
      "id": "L3-001",
      "verdict": "valid",
      "premises_used": ["P1", "P2"],
      "invalid_step": "none",
      "scope_status": "preserved",
      "reason": "結論保留了資料集與方法限制，沒有超出前提。",
      "safer_conclusion": "原結論可保留。"
    }
  ]
}
```

會阻擋 final review 的 L3b verdict：

- `overgeneralized`
- `causal_jump`
- `missing_condition`
- `unsupported`
- `unclear`

## 推論有效性附錄

`--reasoning-appendix` 會產生一個 Markdown 骨架：

```md
## 推論有效性審查附錄

| ID | 原結論 | 已驗證前提 | 推論檢查 | 限制條件 | Verdict | 問題 | 安全結論 |
|---|---|---|---|---|---|---|---|
```

建議把模型完成後的 L3b 結果放在報告後段，而不是塞進主要敘事。主報告保持可讀，附錄負責透明化推論鏈。

附錄規則：

- 只能審正文既有 claim。
- 只能使用 L1/L2 已提供的 evidence/context。
- 若 verdict 不是 `valid`，安全結論必須縮小範圍或要求補證據。
- 附錄不得覆寫 L1 分數。

## Final Review Merge

正式閉環審查分兩步。

第一步：產生 packet。

```powershell
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit.json --semantic-packet --argument-packet --reasoning-packet --reasoning-appendix
```

第二步：把模型 verdict 回灌。

```powershell
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit_closed.json --semantic-verdicts report.semantic_verdicts.json --reasoning-verdicts report.reasoning_verdicts.json --final-review-out report.final_review.json --require-model-reviews
```

`--require-model-reviews` 代表 L2/L3 verdict 檔是必要條件。如果缺少 verdict，final review 會產生 blocking issue。

final review 的接受規則：

```text
L1 passed
+ L2 no blocking semantic issues
+ L3 no blocking reasoning issues
= final passed
```

## Rewrite Loop

建議修改報告時採用這個循環：

```text
1. 寫出初稿 report.md
2. 跑 L1 audit + packets
3. 如果 L1 failed，根據 *_audit.json 與 *.audit_rewrite_contexts.json 修改報告
4. 如果 L1 passed，交給模型填 L2/L3 verdict
5. 回灌 verdict 產生 final_review
6. 若 final_review failed，刪除、縮小或補證據後重跑
7. 直到 final_review passed
```

不要只改分數。每次修正都應回到原文與 citation，看 claim 是否能被支撐、推論是否保留限制條件。

## CLI 選項摘要

```text
--source SOURCE                     原論文抽文，用於數字與 evidence checks
--threshold THRESHOLD               通過分數，預設 80
--json-out JSON_OUT                 寫出 audit JSON
--check-links                       檢查 URL 是否可連線
--no-verify-citation-metadata       關閉預設 citation metadata 驗證
--write                             通過時把 audit score 附加到報告
--rewrite-packet                    產生重寫上下文 packet
--semantic-packet                   產生 L2 semantic support packet
--argument-packet                   產生 L3a argument normalization packet
--reasoning-packet                  產生 L3b inference validity packet
--reasoning-appendix                產生推論有效性附錄骨架
--semantic-verdicts PATH            讀入 L2 model verdict JSON
--reasoning-verdicts PATH           讀入 L3 model verdict JSON
--final-review-out PATH             寫出 final review JSON
--require-model-reviews             缺 L2/L3 verdict 時視為 blocking
--fail-on-audit-fail                審查不通過時 exit code 2
```

## Smoke Test

每次改 auditor 後都應跑：

```powershell
python tools/smoke_test_auditor.py
```

通過時會看到：

```text
smoke tests passed
```

目前 smoke test 覆蓋：

- 空報告會被 hard gate 擋下。
- 無 citation 報告會被 hard gate 擋下。
- 完整報告可以通過 hard gates。
- L3a argument packet 含 normalization schema。
- L3b reasoning packet 含 appendix contract。
- 缺少 required model reviews 時 final review 會阻擋。
- L3 blocking verdict 會阻擋 final pass。

## Codex Skill 安裝方式

若要讓 Codex 直接使用這個 skill，可把 `skills/paper-deep-researcher` 複製到：

```text
C:\Users\<你的使用者名稱>\.codex\skills\paper-deep-researcher
```

之後在 Codex 中處理論文調研、citation-grounded report、審查並修改時，就可以沿用這套流程。

## 實務建議

- 報告預設使用繁體中文。
- 英文術語可以保留，但第一次出現時建議補中文解釋與縮寫全名。
- 不要在最終報告輸出 Query Matrix；它是內部規劃工具。
- 能用 code 計算的審核項目不要交給模型算。
- 模型可以做 L2/L3，但不能自行覆寫 L1 分數。
- 重要 claim 盡量一 claim 一 citation，不要讓 citation 只掛在段落最後。
- PowerShell 顯示亂碼不一定代表檔案壞掉，請用 UTF-8 明確讀取驗證。
- 若報告審查不通過，建議使用「審核結果 + 原文 + 報告內容」一起改寫，再重新跑審查直到通過。

## 常見問題

### 為什麼要分 L3a 和 L3b？

因為「把自然語言 claim 拆成前提與結論」和「判斷前提是否推出結論」是兩個不同工作。L3a 只做 argument normalization，避免模型一邊補前提一邊下 verdict。L3b 才做 validity review。

### 模型推論會不會和前面的審查衝突？

不會，只要分層清楚。L1/L2 負責前提是否有證據；L3 只負責如果這些前提成立，結論是否推出。L3 不得改 L1 分數，也不得把 unsupported premise 當成已證實事實。

### 可以直接審 PDF 嗎？

建議先把 PDF 抽成 UTF-8 markdown 或 text，再用 `--source` 傳給 auditor。這樣數字檢查、citation context、修改循環都比較穩。

### MD 還是 TXT 比較好？

報告用 Markdown。原文抽取檔也建議用 Markdown，因為可以保留 page markers、section markers 與表格上下文。純 TXT 可用，但後續引用定位較弱。
