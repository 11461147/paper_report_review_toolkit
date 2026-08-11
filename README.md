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
    └── report_auditor.py
```

## 用途

- `skills/paper-deep-researcher/`：論文深度調研與中文報告撰寫 skill。
- `tools/report_auditor.py`：報告完成後的純 code 審核工具。

## 審核工具流程

建議所有執行都顯式使用 UTF-8：

```powershell
$env:PYTHONUTF8="1"
$env:PYTHONIOENCODING="utf-8"
python tools/report_auditor.py report.md --source paper_extracted.txt --json-out report_audit.json --rewrite-packet --semantic-packet
```

常用輸出：

- `*_audit.json`：完整審核結果與分數。
- `*.audit_rewrite_contexts.json`：未通過時給 AI 重寫用的問題上下文。
- `*.semantic_audit_packet.json`：給 L2 語意審核使用的 packet。

通過條件：

- `final_score >= 80`
- 沒有 `critical` finding

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
