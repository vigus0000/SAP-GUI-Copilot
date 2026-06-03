# 📝 SAP GUI Copilot - Vibe Coding Plan

## 🚀 1. 專案概述 (Project Overview)
本專案旨在打造一個「SAP GUI 專屬的 GitHub Copilot」。透過外掛形式與 SAP GUI 進行深度整合，結合大語言模型 (LLM) 與 Agentic Workflow，徹底顛覆傳統 ERP 陡峭的學習曲線與繁瑣的操作流程。

### 核心價值
* **No-UI 體驗**：用自然語言取代 T-Code 與複雜的欄位填寫。
* **SOP 自動化**：將資深員工的經驗無縫轉化為企業自動化資產。
* **即時感知**：AI 具備「視覺」與「聽覺」，能感知當前 SAP 畫面狀態與報錯。

---

## 🛠️ 2. 技術棧與系統架構 (Tech Stack & Architecture)

### 基礎設施
* **語言**：Python 3.10+
* **核心依賴**：`pywin32` (控制 SAP GUI COM Interface), `pythoncom` (處理背景事件監聽)
* **AI/LLM 框架**：OpenAI SDK / LangChain / LangGraph (用於 Agent 流程控制)
* **前端介面 (預計 Phase 3 導入)**：`PyQt6` 或 `CustomTkinter` (開發懸浮於 SAP 旁邊的對話框)

### 系統模組架構
1. **SAP Connector (`sap_core.py`)**：負責管理 SAP Session 連線、錯誤處理。
2. **Scanner & Actor (`sap_agent_tools.py`)**：負責「DOM 樹狀遍歷」抓取畫面元件 (Scan)，以及執行填值/點擊 (Action)。
3. **Event Monitor (`sap_monitor.py`)**：使用 `WithEvents` 進行背景監聽使用者的鍵盤與滑鼠操作。
4. **Agent Orchestrator (`llm_brain.py`)**：負責意圖識別、工具呼叫 (Function Calling)、RAG 檢索。

### 套件處理
1. 當前已使用`uv venv`建立虛擬環境
2. 使用`uv pip`安裝套件

---

## 🎯 3. 四大核心模式設計 (Core Modes Definition)

### 🔴 1. Record Mode (錄製模式)
* **目標**：在背景記錄使用者在 SAP 中的所有點擊與輸入，轉化為 AI 可讀的 SOP 腳本。
* **技術**：透過 `win32com.client.WithEvents` 綁定 `SAPSessionEvents`。
* **AI 任務**：將紀錄下來的 raw 動作 (如：輸入 "1000" 到 `ctxtVBAK-VBELN`) 透過 LLM 參數化為 Python Skill (`create_sales_order(sales_org="1000")`)。

### 🟢 2. Ask Mode (問答模式)
* **目標**：根據使用者**當前所在的畫面**，回答操作問題。
* **技術**：Context-Aware RAG。當使用者提問時，觸發 Screen Scanner，擷取當前 T-Code、焦點欄位 (`SystemFocus.Id`) 與狀態列報錯 (`wnd[0]/sbar`)。
* **AI 任務**：結合畫面 JSON 狀態與企業內部 SOP 文件，精準回答「這格該填什麼」或「為何報錯」。

### 🔵 3. Study Mode (教學模式)
* **目標**：步步引導新手操作 SAP 流程。
* **技術**：讀取 Record Mode 產生的腳本，使用 SAP GUI Scripting API 的 `.Visualize(True)` 方法，讓目標輸入框/按鈕在畫面上閃爍紅框。
* **體驗**：在 Copilot 對話框顯示步驟，使用者每完成一步，自動觸發下一步閃爍。

### 🟣 4. Auto Mode (自動代操 Agent)
* **目標**：接收自然語言指令，AI 自主完成 SAP 流程。
* **技術**：Dynamic Screen Exploration (動態畫面探索)。
* **AI 任務 (ReAct Loop)**：
  1. 掃描當前畫面 (`Scan`).
  2. 思考下一步需要填寫什麼欄位或點擊什麼按鈕 (`Think`).
  3. 呼叫 Python Tool 執行動作 (`Act`).
  4. 驗證狀態列或畫面是否跳轉成功 (`Verify`).

---

## 🗺️ 4. 開發階段藍圖 (Implementation Phases)

### 🏁 Phase 1: 基礎建設與 Auto Mode 雛形 (CLI 版本)
* [x] 建立 `sap_core.py`，實作安全的 SAP 連線獲取機制 (重用現有 Session)。
* [x] 實作 `scan_sap_screen()`，將 SAP 畫面轉換為結構化 JSON (保留 Type, Text, Tooltip, ID)。
* [x] 實作基礎 Tools：`set_text(id, value)`, `click(id)`, `send_vkey(key)`.
* [x] **目標驗證**：各 mode 已完成實測，可進入 v0.4.0 / Phase 3。

### 🏁 Phase 2: 監控系統與 Record/Ask Mode
* [x] 建立 `sap_monitor.py`，採用 polling snapshot diff 取代不穩定的 COM event。
* [x] 實作 T-Code、screen、window、field、status message 變化偵測。
* [x] 將監聽到的 Log 寫入本地 JSON 檔案。
* [x] **目標驗證**：Record / Ask / Auto / Study mode 已完成實測。

### 🏁 Phase 3: 視覺化引導與 Study Mode (v0.4.1 完成)
* [x] 實作 `.Visualize(True)` 的高亮標示功能，封裝為 `visualize_element()`。
* [x] 建立「技能庫 (Skill Library)」讀取機制，支援 `skills/` 與 `recordings/`。
* [x] Study Mode 畫面跳轉步驟加入動態驗證，避免 F8/Enter 送出成功但實際被彈窗或必填欄位擋住時誤判完成。
* [x] 強化 Study Mode 的步驟提示與欄位說明，優先使用 scan `fields` 的 SAP label / tooltip / name，降低使用者只看到元件 ID 的情況。
* [x] 針對 popup 補欄位流程建立 guided recovery，讓使用者能在必填彈窗中逐欄高亮、填值、驗證，並重新確認原 SOP 目標是否達成。
* [x] Study Mode 改為互動式參考引導：錄製 SOP 只作為流程、欄位與參考值來源，不再作為絕對操作準則。
* [x] **目標驗證**：啟動教學模式，SAP 畫面上的特定欄位能依序閃爍並提示使用者輸入。

### 🏁 Phase 4: UI 整合與最終封裝
* [ ] 使用 PyQt6 / CustomTkinter 建立一個極簡的懸浮對話框。
* [ ] 實作「貼齊 SAP 視窗邊緣」的功能。
* [ ] 將四大模式整合至 UI 切換。

---

## 🧩 4.5 待處理清單 (Backlog)

* [ ] **ABAP editor 寫入後驗證**
  * 現況：`set_editor_text()` 可讓 Hello World 流程完成儲存、啟用與執行，但 SAP GUI Frontend 可能在插入文字時回報例外。
  * 風險：工具端無法可靠判斷 editor 內容是否已完整寫入，只能從後續儲存/啟用/執行結果間接推斷。
  * 待補：新增 editor read-back / compare 機制；若 SAP GUI COM 無法讀回，至少在貼上後加入狀態列、啟用結果、執行輸出等交叉驗證。

---

## ⚠️ 5. AI 開發指引 (Vibe Coding Guidelines)

致協助開發的 AI Agent，請在撰寫程式碼時嚴格遵守以下原則：

1. **SAP GUI COM 防禦性編程**：
   * 每次呼叫 `session.FindById()` 都有可能因為畫面切換而拋出 `com_error`，請務必使用 `try-except` 包裝，並提供優雅的錯誤處理。
   * SAP 物件的狀態隨時會改變，不要在全域變數快取 UI 元件，需要時隨時重新 Scan。
2. **Token 優化**：
   * `scan_sap_screen()` 抓出的元件很多，傳給 LLM 之前，務必過濾掉無用的排版元件 (如 `GuiContainer`, `GuiCustomControl` 等)，只保留可互動的 `GuiTextField`, `GuiButton`, `GuiTab` 等。
3. **安全性考量**：
   * Auto Mode 執行重大寫入操作 (如：點擊 Save / 發送單據) 前，必須加入 `Human-in-the-loop` (人工確認) 的回調機制，不可讓 AI 直接把爛資料寫入 SAP 核心。
4. **架構解耦**：
   * 保持 SAP 操作邏輯 (Tools) 與 LLM 推理邏輯分離，確保未來抽換 LLM 模型 (OpenAI -> Claude -> 本地模型) 時不影響核心功能。

