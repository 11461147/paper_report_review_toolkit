# 論文報告撰寫與審核 Toolkit

這個資料夾是從目前專案整理出的獨立版本，方便複製到其他專案使用。

## 內容

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

## 用途

- `skills/paper-deep-researcher/`：論文深度調研與中文報告撰寫 skill。
- `tools/report_auditor.py`：報告完成後的純 code 審核工具。
- `tools/smoke_test_auditor.py`：快速檢查 hard gates、L2/L3 packet 與 final review gating 是否退化。

## v0.4 新增重點

- Hard gates：空報告、低主張數、低 citation 主張數、低 citation 支撐率、缺必要章節或缺 references 會直接 fail。
- L2 semantic packet：把可疑 claim 與 citation/context 包成語義支撐審查任務。
- L3a argument packet：先把自然語言 claim 正規化成 premises、conclusion、scope conditions、inference rule、implicit assumptions 與 missing premises。
- L3b reasoning packet：檢查已驗證前提是否能推出結論，並要求保留 scope conditions。
- Final review merger：可把模型產生的 L2/L3 verdict JSON 回灌，讓 blocking verdict 阻擋最終通過。

## 審核工具流程

建議所有執行都顯式使用 UTF-8：

```powershell
$env:PYTHONUTF8="1"
$env:PYTHONIOENCODING="utf-8"
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit.json --rewrite-packet --semantic-packet --argument-packet --reasoning-packet --reasoning-appendix
```

常用輸出：

- `*_audit.json`：完整審核結果與分數。
- `*.audit_rewrite_contexts.json`：未通過時給 AI 重寫用的問題上下文。
- `*.semantic_audit_packet.json`：給 L2 語義支撐審核使用的 packet。
- `*.argument_packet.json`：給 L3a 論證正規化使用的 packet，先輸出 premises、conclusion、scope conditions、inference rule 與 missing premises。
- `*.reasoning_audit_packet.json`：給 L3b 推論有效性審核使用的 packet。
- `*.reasoning_appendix.md`：可放在報告正文後面的推論有效性審查附錄骨架。
- `*.final_review.json`：合併 L1/L2/L3 的最終閉環審查結果。

通過條件：

- `final_score >= 80`
- 沒有 `critical` finding
- hard gates 全部通過
- 若啟用正式閉環審查，L2/L3 沒有 blocking verdict

## 分層審查架構

這個 toolkit 不主張單靠 deterministic auditor 判斷論文價值。它把報告可靠性拆成分層守門：

```text
L1 deterministic evidence checks
  檢查 citation、URL、DOI/arXiv、數字、章節、格式、metadata、亂碼與空引用。

L2 semantic support review
  模型只檢查 citation/context 是否真的支撐 claim，不重算分數。

L3a argument normalization
  模型先把自然語言 claim 拆成 premises、conclusion、scope conditions、inference rule、implicit assumptions 與 missing premises；不判斷前提真偽。

L3b inference validity review
  模型檢查已驗證前提能否推出結論，避免 overgeneralization、causal jump、
  missing condition、scope creep。
```

建議報告正文保持可讀，不把形式化推論表塞進主敘事。L3 輸出應整理到報告後段附錄：

```md
## 推論有效性審查附錄

| ID | 原結論 | 已驗證前提 | 推論檢查 | 限制條件 | Verdict | 問題 | 安全結論 |
|---|---|---|---|---|---|---|---|
```

附錄規則：

- 只能審正文既有 claim，不替正文補新證據。
- 只能使用 L1/L2 已提供的 evidence/context。
- 若 verdict 不是 `valid`，安全結論必須縮小範圍或要求補證據。
- 附錄不得覆寫 L1 分數；它是 blocking review / rewrite guide。

正式閉環審查分兩步：

```powershell
# 1. 先產生 L2/L3 packet，交給模型填 verdict
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit.json --semantic-packet --argument-packet --reasoning-packet --reasoning-appendix

# 2. 將模型 verdict 回灌，產生 final_review，並把 blocking verdict 納入 pass/fail
python tools/report_auditor.py report.md --source paper_extracted.md --json-out report_audit_closed.json --semantic-verdicts report.semantic_verdicts.json --reasoning-verdicts report.reasoning_verdicts.json --final-review-out report.final_review.json --require-model-reviews
```

模型 verdict 檔可以是含 `items`、`reviews`、`verdicts` 或 `results` 的 JSON。L2 blocking 條件包含 `evidence_status` 為 `unsupported` / `unverifiable`，或 `action` 為 `delete` / `replace_source`；L3 blocking 條件包含 `overgeneralized`、`causal_jump`、`missing_condition`、`unsupported`、`unclear`。L2/L3 不重算 L1 分數，只能阻擋最終接受或提供重寫方向。

結論只在下列條件同時成立時才視為可接受：

```text
verified source evidence
+ complete premises
+ valid inference
+ preserved scope conditions
= conclusion acceptable within evidence scope
```

## Hard Gates

v0.2 起，通過不再只看扣分後的加權分數。以下情況會直接 fail：

- 報告短於 `600` 字元。
- 可審核主張少於 `3` 條。
- 有 citation 的主張少於 `1` 條。
- citation 支撐率低於 `35%`。
- 必要章節覆蓋率低於 `60%`。
- 參考來源區沒有可辨識文獻條目。

可用 smoke test 快速確認 hard gates 沒有退化：

```powershell
python tools/smoke_test_auditor.py
```

## Codex skill 安裝方式

若要讓 Codex 直接使用這個 skill，可把 `skills/paper-deep-researcher` 複製到：

```text
C:\Users\<你的使用者名稱>\.codex\skills\paper-deep-researcher
```

## 注意事項

- 報告預設使用繁體中文。
- 不要在最終報告輸出 Query Matrix。
- 能用 code 計算的審核項目不要交給模型算。
- 若審核後需要修改，建議使用「審核報告 + 原文 + 報告內容」一起重寫，再重新跑審核直到通過。
- PowerShell 顯示亂碼不一定代表檔案壞掉，請用 UTF-8 明確讀取驗證。
