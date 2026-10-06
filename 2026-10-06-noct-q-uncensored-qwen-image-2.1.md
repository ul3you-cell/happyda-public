# Noct-Q-Uncensored-Qwen-Image-2.1：在 Mac Mini M4 32GB 上跑起來

研究日期：2026-10-06（CST, UTC+08:00）
目標機器：**Mac mini M4 標準版，32 GB 統一記憶體**

---

## 一句話結論

在 M4 Mac mini 32 GB 上跑 Noct-Q-Uncensored-Qwen-Image-2.1 有**兩條主路徑**：

1. **ComfyUI + GGUF（推薦入門）**——社群 GGUF 版本、跨平台、Apple Silicon 走 MPS／Metal，文件與範例較多。32 GB 統一記憶體有較多餘裕，但是否穩定仍要以實跑為準。
2. **Core ML（Apple 原生）**——Devin Lai 的 `Qwen-Image-2.1-Coreml` 在 M5 32 GB 的單一公開基準比 PyTorch MPS 快 2.4–2.6×；macOS 15+ 必要，**M4 還沒有正式基準**，得自己測。

> ⚠ **授權注意**：Qwen Research License 限**非商業使用**。不要以可辨識真人製作私密或性化影像，尤其不得涉及未成年或未經同意內容。

---

## 1. 模型是什麼（30 秒版本）

- **Noctaluna** 把 `Qwen/Qwen-Image-2.1` 的 transformer 權重改過，產出**無需 LoRA 就能生成裸體與成人場景**的單檔 checkpoint。
- HF repo 已標記 **Not-For-All-Audiences**。
- 主模型：`NoctQ_V4_int8_convrot.safetensors`（7.3 GB，V4；V3 為舊版同名不同 hash）。
- 完整圖庫、不同精度（int4 / fp8 / bf16 / fp16）與評測在 Civitai。

> 不接受這類用途的話，直接跑官方 `Qwen/Qwen-Image-2.1` 即可，步驟幾乎一樣。

---

## 2. 兩條路線對比

| | ComfyUI + GGUF | Core ML（devin-lai） |
|---|---|---|
| 平台 | 跨平台 | Apple Silicon only |
| macOS | 12+ | **15+**（必要） |
| Python | 3.10+ | 3.11–3.13 |
| 速度（M 系列） | 中等，可用量化模型降低記憶體壓力 | 專案在 M5 32 GB 的單一基準跑 1024² 比 MPS 快 2.4–2.6×；不能直接推論到 M4 |
| 工作流 | 拖拉節點、視覺化 | 命令列 CLI + Python API |
| 編輯（img2img） | ✅ 官方編輯模板 | ❌ 目前只支援文生圖 |
| 自訂 prompt | ✅ | ✅（要裝可選 text encoder，依賴較多） |
| **M4 32 GB 實測資料** | 沒有可重現的公開 M4 32 GB 基準 | 該專案公開基準只測 M5，**M4 要自己跑一次** |
| 第一選擇 | **第一次跑就選這條** | 已經會用、想榨效能再來 |

---

## 3. 路徑 A：ComfyUI + GGUF（推薦）

### 3.1 系統需求（Mac mini M4 32 GB）

- macOS 15 或更新（ComfyUI 在 macOS 14+ 都能跑，15+ 較穩）
- Python 3.10 以上（建議 3.11）
- 磁碟空間：模型本體約 **15–20 GB**（主模型 + 文字編碼器 + VAE）
- 首次安裝 ComfyUI 與依賴：另加 **5–10 GB**
- **MPS / Metal 是必要**——M4 內建 GPU 會被 ComfyUI 自動偵測

### 3.2 安裝 ComfyUI

在終端機：

```bash
# 用 Homebrew 裝 Python（如果還沒有）
brew install python@3.11 git

# 把 ComfyUI 抓下來
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI

# 建立虛擬環境（建議，避免污染系統 Python）
python3.11 -m venv .venv
source .venv/bin/activate

# 安裝 PyTorch（Apple Silicon 版）
pip install --upgrade pip
pip install torch torchvision torchaudio

# 安裝 ComfyUI 依賴
pip install -r requirements.txt
```

> PyTorch 在 Apple Silicon 上會走 MPS。**不要裝 NVIDIA CUDA 版**，對 M 系列沒用。

### 3.3 安裝 GGUF 載入插件

Noct Q 社群有提供 GGUF 版本（`abenzerps/Qwen-Image-2.1-Uncensored-GGUF`）。要在 ComfyUI 用 GGUF 格式，要裝插件：

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/leejet/ComfyUI-GGUF.git
pip install -r ComfyUI-GGUF/requirements.txt
```

### 3.4 下載模型（總計約 14 GB）

| 檔案 | 大小 | 放哪 |
|---|---|---|
| Noct Q 社群 GGUF（Q4 或 Q8，看你顯存） | 7–9 GB | `ComfyUI/models/diffusion_models/` |
| `qwen3vl_8b_int8_convrot.safetensors` | 約 8 GB | `ComfyUI/models/text_encoders/` |
| `qwen_image_2.1_vae_bf16.safetensors` | 約 0.5 GB | `ComfyUI/models/vae/` |

下載連結（已用瀏覽器登入 HF 即可下載，或用 `huggingface-cli`）：

- 社群 GGUF（Noct Q）：https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF
- 官方文字編碼器（Comfy-Org）：https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/text_encoders/qwen3vl_8b_int8_convrot.safetensors
- 官方 VAE：https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/vae/qwen_image_2.1_vae_bf16.safetensors

> 32 GB 統一記憶體：建議 **GGUF Q4**（約 5–6 GB）給主模型，留更多記憶體給文字編碼器與系統。
> 如果不在意速度、要最高品質，可以選 GGUF Q8 或原版 `NoctQ_V4_int8_convrot.safetensors`（7.3 GB）。

用 `huggingface-cli`：

```bash
pip install -U "huggingface_hub[cli]"
huggingface-cli download abenzerps/Qwen-Image-2.1-Uncensored-GGUF \
  --local-dir ComfyUI/models/diffusion_models/ \
  --include "qwen-image-2.1-UC-Q4_K_M.gguf"
huggingface-cli download Comfy-Org/Qwen-Image-2.1 \
  --local-dir ComfyUI/models/ \
  --include "text_encoders/qwen3vl_8b_int8_convrot.safetensors" \
           "vae/qwen_image_2.1_vae_bf16.safetensors"
```

### 3.5 啟動 ComfyUI

```bash
cd ComfyUI
source .venv/bin/activate
python main.py
```

第一次啟動會在背景跑 `pip install` 補依賴，看到 `To see the GUI go to: http://127.0.0.1:8188` 就開瀏覽器進去。

### 3.6 載入工作流

兩個方法（選一個）：

1. **拖拉 JSON**：從 HF repo 抓 `NoctQ_V4_workflow.json`，直接拖到 ComfyUI 視窗。
2. **用官方模板**：在 ComfyUI 視窗的 **Workflow → Browse Templates** 搜 "Qwen-Image-2.1"，會看到 Comfy-Org 官方 T2I / Edit / 背去背三個模板。

官方模板預設用**官方** Qwen-Image-2.1；要跑 Noct Q 改兩個 Loader 節點：

- 把 `Load Diffusion Model` 換成 `Unet Loader (GGUF)`，選 Noct Q GGUF
- 把 `CLIPLoader` 的類型選 `qwen_image`

### 3.7 建議參數

依官方 Comfy-Org 模板與 Noct Q 卡片：

| 項目 | 推薦值 |
|---|---|
| Sampler | `euler` |
| Scheduler | `simple` |
| Steps | **25**（官方模板預設） |
| CFG | **1**（官方模板預設，跳過 negative pass） |
| 解析度 | 1024 × 1536 直幅（Noct Q 推薦）或 1024 × 1024 |

> 想要 negative prompt 生效，CFG 改成 3；速度約慢一倍。

### 3.8 啟動低記憶體模式（必要時）

如果 32 GB 跑到一半 OOM：

```bash
python main.py --lowvram
```

或加大強制 offload：

```bash
python main.py --preview-method auto --use-pytorch-cross-attention
```

> 32 GB 跑 Q4 通常較有餘裕，但模型版本、解析度與同時開啟的程式都會影響記憶體；第一次請以低負載環境實跑確認。

---

## 4. 路徑 B：Core ML（Apple 原生；M4 須自行實測）

> 適合：只用 T2I（不要編輯）、可以接受命令列，且願意在 M4 上自行量測速度與記憶體用量。

### 4.1 系統需求

- **macOS 15 或更新**（必要，更舊不支援新 Core ML 功能）
- Python 3.11、3.12 或 3.13
- 磁碟空間：模型包 **14.74 GB** + 依賴與編譯空間

### 4.2 安裝

```bash
git clone https://github.com/devin-lai/Qwen-Image-2.1-Coreml.git
cd Qwen-Image-2.1-Coreml
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

下載預轉好的 Core ML 套件：

```bash
python download_models.py
```

第一次跑會編譯 Core ML 模型，較慢；之後會 cache。

### 4.3 第一次出圖

```bash
python generate.py --out neon.png
```

這會用 repo 內建的 neon 招牌 prompt 出 1024 × 1024 圖，40 steps。

預設 `cpu_and_gpu`（GPU 加速）。要全 GPU 顯式指定：

```bash
python generate.py --out neon.png --compute-units cpu_and_gpu
```

### 4.4 自訂 prompt

裝可選依賴 + 編碼 prompt 一次：

```bash
pip install '.[torch-reference]'
python encode_prompt.py "a lighthouse in a storm, long exposure" --name lighthouse
python generate.py --prompt-embeds assets/prompts/lighthouse.npz --out lighthouse.png
```

> ⚠ encode 階段會另外下載官方 Qwen3-VL 文字編碼器，記憶體用量可能比生成更高；32 GB 較有餘裕，但仍應以第一次實跑為準。

### 4.5 M4 實測尚未公開

該專案公開基準只測了 M5 32 GB，FAQ 明寫：**M1/M2/M3/M4 還沒正式基準**。如果你跑起來，建議回 issue 填 hardware report 幫社群補資料。

---

## 5. 路徑選擇決策樹

```
你是第一次跑 Qwen-Image 系列嗎？
  ├─ 是 → 路徑 A（ComfyUI）
  │       ├─ 要編輯既有圖片嗎？
  │       │   ├─ 是 → 用 Comfy-Org 官方 image_edit 模板
  │       │   └─ 否 → 用官方 t2i 模板
  │       └─ 跑得很慢？
  │           ├─ Q8 太慢 → 換 Q4 GGUF
  │           └─ 還是不順 → 關閉其他重度程式，並改用較小量化模型後重測
  │
  └─ 已經會用、想榨效能 → 路徑 B（Core ML）
        └─ 之後若要編輯 → 回到 ComfyUI
```

> 32 GB 統一記憶體其實很夠。如果跑 ComfyUI 時瀏覽器、Slack 等同時在吃 RAM，可以先用 **Q4 GGUF** 留 buffer；之後熟手了再換 Q8。

---

## 6. 你的硬體（M4 32 GB）額外注意

- **統一記憶體是共享的**：瀏覽器、Slack、VS Code 與圖像模型共用同一池記憶體。跑圖前先關掉重度程式，並以系統實際記憶體壓力為準。
- **MPS 不等於 CUDA**：有些 PyTorch op 在 MPS 上可能未實作或較慢。GGUF 是另一種載入方式，不能保證解決每一種 MPS 問題。
- **不要把媒體引擎當成出圖速度指標**：Apple 規格中，M4 為 10 核 GPU、120 GB/s 記憶體頻寬；M4 Pro 為 16 核 GPU、273 GB/s。兩者都有影片媒體引擎，但其規格不能直接換算成圖像生成快多少。
- **Core ML 的 M4 數據仍缺**：目前公開基準是 M5 MacBook Pro 32 GB；第一次跑時請記錄相同解析度與 steps 的耗時，再決定是否改走這條路。

---

## 7. Prompt 怎麼寫

Qwen-Image-2.1（以及 Noct Q）吃**敘述文**，不吃 tag 列表。模板：

```
[構圖/鏡頭] + [主體（含明確年齡）] + [動作] + [場景] + [光線] + [風格]
```

範例：

> A close three-quarter portrait of a woman in her late twenties sitting on a sunlit balcony, soft morning light, shallow depth of field, photographic style.

進階：

> A low-angle wide shot of a man in his early thirties jogging along a wet seaside boardwalk at dawn, orange sodium streetlights reflecting on the puddles, soft mist, cinematic.

> 一張 35mm 街拍：夜晚的香港霓虹巷弄裡一位二十多歲的女性撐著透明傘，雨水在路面反射霓虹色彩，淺景深，菲林顆粒。

---

## 8. 疑難排解速查

| 症狀 | 可能原因 | 解法 |
|---|---|---|
| ComfyUI 啟動後看不到 GGUF loader | 沒裝 ComfyUI-GGUF | §3.3 |
| 出圖全黑 / 全噪訊 | VAE 沒選對 / 解析度非 32 倍數 | 確認 VAE 檔名、解析度 |
| OOM / 程式被殺 | 統一記憶體被其他程式佔走 | 關其他程式；換 Q4；加 `--lowvram` |
| Core ML 報 `computeUnits` 錯誤 | macOS < 15 | 升 macOS 或改用路徑 A |
| PyTorch 報 "MPS backend not available" | 沒裝 Apple Silicon 版 PyTorch | `pip install --upgrade torch`（不要 CUDA 版） |
| 文字渲染奇怪 | CFG 太高 | 試 CFG 1（預設）或 2 |
| 中文出圖文字糊 | Q4 量化掉字 | 換 Q8 或原版 int8 |

---

## 9. 為什麼 Noct Q 這條路線要小心

- **授權**：Qwen Research License 限**非商業**。商業用途要直接洽阿里拿商用授權。
- **真人肖像與隱私**：不要以真人製作私密或性化影像；適用法律會隨地點、散布方式與同意情況而不同，具體情況應向合格法律專業人士確認。
- **平台政策**：X、Instagram、Discord、Threads 等平台可能限制或移除這類內容，並可能對帳號採取處分；發布前應先確認當下規範。HF repo 也標示 `Not-For-All-Audiences`。
- **別拿來做小孩或非同意的內容**：這些情況涉及嚴重傷害與重大法律風險，絕對不要做。

---

## 10. 來源與擷取日期

| 編號 | 來源 | 等級 | 擷取 |
|---|---|---|---|
| 1 | https://huggingface.co/Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1/blob/main/README.md | 已讀原文 | 2026-10-06 |
| 2 | https://huggingface.co/spaces/MikeIck/Noct-Q-Uncensored-Qwen-Image-Demo | 已讀原文 | 2026-10-06 |
| 3 | https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1 | 已讀原文 | 2026-10-06 |
| 4 | https://github.com/devin-lai/Qwen-Image-2.1-Coreml | 已讀原文 | 2026-10-06 |
| 5 | https://medium.com/@hyperai/qwen-image-2-1-uncensored-local-deployment-a-16gb-macbook-air-can-run-it-3a98394f7bf4 | 已讀原文 | 2026-10-06 |
| 6 | https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF | 僅搜尋摘要 | 2026-10-06 |
| 7 | https://huggingface.co/Comfy-Org/Qwen-Image-2.1 | 僅搜尋摘要 | 2026-10-06 |
| 8 | https://support.apple.com/en-us/121555 | Apple 技術規格原文 | 2026-10-06 |
| 9 | https://huggingface.co/api/models/abenzerps/Qwen-Image-2.1-Uncensored-GGUF | HF API 檔案清單原文 | 2026-10-06 |

---

## 附錄：交給 dev profile 執行的指令稿

scout 不做部署，這份指令稿**等 HTML / README 確認後**，請轉交 dev 或 default main profile 執行（安裝 ComfyUI、抓模型、推到 GitHub Pages）：

```bash
# 1. 確認 macOS 版本 ≥ 15（Core ML 路徑必要；ComfyUI 路徑 14+ 也行）
sw_vers

# 2. 確認 Xcode CLI Tools
xcode-select -p || xcode-select --install

# 3. 確認 Homebrew 與 Python 3.11
brew --version
brew install python@3.11 git

# 4. 抓 ComfyUI
git clone https://github.com/comfyanonymous/ComfyUI.git ~/ComfyUI
cd ~/ComfyUI
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 5. 裝 GGUF 插件
cd custom_nodes
git clone https://github.com/leejet/ComfyUI-GGUF.git
pip install -r ComfyUI-GGUF/requirements.txt
cd ../..

# 6. 抓模型（Q4 GGUF + 官方 text encoder + VAE）
pip install -U "huggingface_hub[cli]"
huggingface-cli download abenzerps/Qwen-Image-2.1-Uncensored-GGUF \
  --local-dir models/diffusion_models --include "qwen-image-2.1-UC-Q4_K_M.gguf"
huggingface-cli download Comfy-Org/Qwen-Image-2.1 \
  --local-dir . \
  --include "text_encoders/qwen3vl_8b_int8_convrot.safetensors" \
           "vae/qwen_image_2.1_vae_bf16.safetensors"
mkdir -p models/text_encoders models/vae
mv text_encoders/qwen3vl_8b_int8_convrot.safetensors models/text_encoders/
mv vae/qwen_image_2.1_vae_bf16.safetensors models/vae/

# 7. 啟動
python main.py

# 8. （另一個終端）部署 HTML 到 GitHub Pages
# 由 dev / default main profile 接手：
#   - 建立 repo（例：noct-q-m4-guide）
#   - 將本檔產出的 index.html 推到 main
#   - Settings → Pages → Deploy from branch → main / (root)
#   - 5–10 分鐘後 https://<user>.github.io/noct-q-m4-guide/ 上線
```

研究 / 文件：scout profile
實作 / 部署：請轉 dev 或 default main
